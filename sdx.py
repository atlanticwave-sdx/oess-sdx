#!/bin/usr/python3
from flask import Flask, request, jsonify
from datetime import datetime, timezone
import requests
import traceback
import sys
import re
import os
import yaml
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

NAME_PREFIX = "OESS-SDX-L2VPN--"
VERSION_FILE = "/tmp/oess_sdx.ver"
timeout = 30
sdx_version = 0
oess2sdx = {}
sdx2oess = {}
sdx_config = None
sdx_topology = None
sdx_topo_conv = {"links": [], "nodes": []}

app = Flask(__name__)

def utcnow():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

def load_config(fallback_prev_config=True):
    global sdx_config
    dirname = os.path.dirname(os.path.abspath(__file__))
    try:
        new_config = yaml.safe_load(open(dirname + '/sdx_config.yml', 'r'))
    except Exception as exc:
        if fallback_prev_config:
            return
        err = traceback.format_exc().replace("\n", ", ")
        raise ValueError("Unable to read config: %s - %s" % (exc, err))
    sdx_config = new_config


def update_version(inc=1):
    global sdx_version
    dirname = os.path.dirname(os.path.abspath(__file__))
    try:
        sdx_version = int(open(VERSION_FILE).read())
    except:
        sdx_version = 1
    sdx_version += inc
    with open(VERSION_FILE, "w") as f:
        f.write(str(sdx_version))


def get_oess_topo():
    """Fetch OESS topology and create references for convenience."""
    topo = {"node_by_id": {}, "link_by_id": {}, "intf_by_id": {}}
    res = requests.get(sdx_config["oess_url"] + "/services/data.cgi?method=get_all_node_status", verify=False, auth=(sdx_config["username"], sdx_config["password"]), timeout=timeout)
    topo["nodes"] = res.json()["results"]
    res = requests.get(sdx_config["oess_url"] + "/services/data.cgi?method=get_all_link_status", verify=False, auth=(sdx_config["username"], sdx_config["password"]), timeout=timeout)
    topo["links"] = res.json()["results"]
    for node in topo["nodes"]:
        topo["node_by_id"][node["node_id"]] = node
        #res = requests.get(sdx_config["oess_url"] + "/services/data.cgi?method=get_node_interfaces&workgroup_id=%s&node=%s&show_down=1&show_trunk=1" % (sdx_config["workgroup_id"], node["name"]), verify=False, auth=(sdx_config["username"], sdx_config["password"]), timeout=timeout)
        #node["interfaces"] = res.json()["results"]
        #for intf in node["interfaces"]:
        #    intf["node"] = node
        #    topo["intf_by_id"][intf["interface_id"]] = intf
        node["interfaces"] = []
    res = requests.get(sdx_config["oess_url"] + "/services/interface.cgi?method=get_workgroup_interfaces&workgroup_id=%s" % (sdx_config["workgroup_id"]), verify=False, auth=(sdx_config["username"], sdx_config["password"]), timeout=timeout)
    interfaces = res.json()["results"]
    for intf in interfaces:
        topo["intf_by_id"][intf["interface_id"]] = intf
        node = topo["node_by_id"][intf["node_id"]]
        node["interfaces"].append(intf)
        intf["node"] = node
    for link in topo["links"]:
        topo["link_by_id"][link["link_id"]] = link
        topo["intf_by_id"][link["interface_a_id"]]["link"] = link
        topo["intf_by_id"][link["interface_z_id"]]["link"] = link
        link["interface_a"] = topo["intf_by_id"][link["interface_a_id"]]
        link["interface_z"] = topo["intf_by_id"][link["interface_z_id"]]
    return topo


def sanitize_name(name):
    # TODO
    return name


def get_intf_config(interface):
    """Get the interface config."""
    interfaces = sdx_config.get("interfaces", {})
    if not interfaces or not isinstance(interfaces, dict):
        return {}
    return interfaces.get(int(interface["interface_id"]), {})


def get_link_config(link):
    """Get link config"""
    links = sdx_config.get("links", {})
    if not links or not isinstance(links, dict):
        return {}
    return links.get(int(link["link_id"]), {})


def get_interface_mtu(interface):
    """Function to try to obtain the MTU of an interface."""
    return get_intf_config(interface).get("mtu", "1500")


def get_interface_state(interface):
    """Function to try to obtain the state of an interface."""
    config_state = get_intf_config(interface).get("state")
    if config_state:
        return config_state
    return get_object_state(interface)


def get_type_port_speed(bandwidth):
    """
    Function to try to obtain the speed of an interface.
    The type enum is 100FE, 1GE, 10GE, 25GE, 40GE, 50GE, 100GE, 400GE, and Other
    """
    juniper_bw_mbps_to_type = {
        "400000": "400GE",
        "100000": "100GE",
        "50000": "50GE",
        "40000": "40GE",
        "25000": "25GE",
        "10000": "10GE",
        "1000": "1GE",
        "100": "100FE",
    }
    return juniper_bw_mbps_to_type.get(bandwidth, "Other")


def get_link_bandwidth(link):
    """Get link bandwidth (Gbps) based on interface speeds (Mbps)."""
    intfa_bw = int(link["interface_a"]["bandwidth"]) / 100
    intfz_bw = int(link["interface_z"]["bandwidth"]) / 100
    return min(intfa_bw, intfz_bw)


def get_port_urn(interface):
   """generate the full urn address for a port"""
   return "urn:sdx:port:%s:%s:%s" % (sdx_config["oxp_url"], interface["node"]["name"], interface["name"])


def get_link_urn_from_interface(interface):
   """Return the first active link on this interface"""
   link = sdx_topology["intf_by_id"].get(interface["interface_id"], {}).get("link")
   if not link:
       return ""
   return "urn:sdx:link:%s:%s" % (sdx_config["oxp_url"], get_link_label(link))


def get_link_label(link):
    """Get the link label"""
    intfa = link["interface_a"]["name"]
    nodea = link["interface_a"]["node"]["name"]
    intfz = link["interface_z"]["name"]
    nodez = link["interface_z"]["node"]["name"]
    if nodea == nodez:
        if intfz < intfa:
            intfa, intfz = intfz, intfa
    elif nodez < nodea:
        nodea, nodez = nodez, nodea
        intfa, intfz = intfz, intfa
    return "%s/%s_%s/%s" % (nodea, intfa, nodez, intfz)


def get_sdx_port(interface):
    sdx_port = {}
    sdx_port["id"] = get_port_urn(interface)
    sdx_port["name"] = interface["name"][:30]
    sdx_port["node"] = "urn:sdx:node:%s:%s" % (sdx_config["oxp_url"], interface["node"]["name"])
    sdx_port["type"] = get_type_port_speed(interface["bandwidth"])
    sdx_port["status"] = get_object_status(interface)
    sdx_port["state"] = get_interface_state(interface)
    sdx_port["mtu"] = int(interface["mtu"])

    intf_config = get_intf_config(interface)
    if interface.get("int_role", "access") == "trunk":
        sdx_port["nni"] = get_link_urn_from_interface(interface)
    elif "sdx_nni" in intf_config:
        sdx_port["nni"] = "urn:sdx:port:" + intf_config["sdx_nni"]
    else:
        sdx_port["nni"] = ""

    vlan_range = intf_config.get("sdx_vlan_range")
    if not vlan_range:
        vlan_range = interface.get("mpls_vlan_tag_range")
        if vlan_range:
            vlans = vlan_range.split("-")
            if len(vlans) == 2 and vlans[0].isdigit() and vlans[1].isdigit():
                vlan_range = [[int(vlans[0]), int(vlans[1])]]
            else:
                vlan_range = None
        if not vlan_range:
            vlan_range = [[1, 4095]]

    sdx_port["services"] = {
        "l2vpn-ptp": {"vlan_range": vlan_range},
        # "l2vpn-ptmp":{"vlan_range": vlan_range}
    }

    sdx_port["private"] = ["status"]

    return sdx_port


def get_object_status(obj):
    """Get object status (up, down, error)."""
    if any([
        obj.get("status") == "up",
        obj.get("operational_state_mpls") == "up",
        obj.get("operational_state") == "up",
    ]):
        return "up"
    if any([
        obj.get("status") == "down",
        obj.get("operational_state_mpls") == "down",
        obj.get("operational_state") == "down",
    ]):
        return "down"
    return "error"


def get_object_state(obj):
    """Get node state (enabled, disabled)."""
    if obj.get("in_maint") == "yes":
        return "maintenance"
    if obj.get("link_state") == "active" or obj.get("admin_state") in ["active", "up"]:
        return "enabled"
    if "int_role" in obj and obj.get("status") == "up":
        return "enabled"
    return "disabled"


def get_sdx_ports(interfaces):
    global sdx2oess, oess2sdx
    sdx_ports = []
    for interface in interfaces:
        sdx_ports.append(get_sdx_port(interface))
        oess2sdx[interface["interface_id"]] = sdx_ports[-1]
        sdx2oess[sdx_ports[-1]["id"]] = interface
    return sdx_ports


def get_sdx_node(node):
    sdx_node = {}
    sdx_node["name"] = sanitize_name(node["name"])
    sdx_node["id"] = "urn:sdx:node:%s:%s" % (sdx_config["oxp_url"], sdx_node["name"])
    sdx_node["location"] = {
        #"address": kytos_node["metadata"].get("address", ""),
        "latitude": float(node["latitude"]),
        "longitude": float(node["longitude"]),
        #"iso3166_2_lvl4": kytos_node["metadata"].get("iso3166_2_lvl4", ""),
        "private": [],
    }
    sdx_node["ports"] = get_sdx_ports(node["interfaces"])
    sdx_node["status"] = get_object_status(node)
    sdx_node["state"] = get_object_state(node)
    return sdx_node


def get_sdx_nodes(oess_topo):
    sdx_nodes = []
    for node in oess_topo["nodes"]:
        sdx_nodes.append(get_sdx_node(node))
    return sdx_nodes


def get_sdx_link(link):
    """generates a dictionary object for every link in a network,
    and containing all the attributes for each link"""
    sdx_link = {}
    sdx_link["name"] = get_link_label(link)
    sdx_link["id"] = "urn:sdx:link:%s:%s" % (sdx_config["oxp_url"], sdx_link["name"])
    sdx_link["ports"] = sorted(
        [
            get_port_urn(link["interface_a"]),
            get_port_urn(link["interface_z"]),
        ]
    )
    sdx_link["type"] = "intra"
    sdx_link["bandwidth"] = get_link_bandwidth(link)
    link_config = get_link_config(link)
    sdx_link["residual_bandwidth"] = link_config.get("residual_bandwidth", 100)
    sdx_link["latency"] = link_config.get("latency", 0)
    sdx_link["packet_loss"] = link_config.get("packet_loss", 0)
    sdx_link["availability"] = link_config.get("availability", 0)
    sdx_link["status"] = get_object_status(link)
    sdx_link["state"] = get_object_state(link)
    sdx_link["private"] = ["packet_loss"]
    return sdx_link


def get_sdx_links(oess_topo):
    sdx_links = []
    for link in oess_topo["links"]:
        sdx_links.append(get_sdx_link(link))
    return sdx_links

def convert_topo(oess_topo):
    return {
        "name": sdx_config["oxp_name"],
        "id": "urn:sdx:topology:%s" % (sdx_config["oxp_url"]),
        "model_version": sdx_config["model_version"],
        "nodes": get_sdx_nodes(oess_topo),
        "links": get_sdx_links(oess_topo),
        "services": ["l2vpn-ptp"],
        "timestamp": sdx_topo_conv.get("timestamp", utcnow()),
        "version": sdx_topo_conv.get("version", 1),
    }


def check_topo_diff(cur_topo, new_topo):
    diff_admin, diff_oper = False, False

    #
    # Nodes
    #
    remain_nodes = {}
    for node in cur_topo["nodes"]:
        remain_nodes[node["id"]] = node
    for new_node in new_topo["nodes"]:
        # node was added
        cur_node = remain_nodes.pop(new_node["id"], None)
        if not cur_node:
            diff_admin = True
            continue
        if any([
            new_node["location"] != cur_node["location"],
            new_node["state"] != cur_node["state"],
        ]):
            diff_admin = True
        if any([
            new_node["status"] != cur_node["status"],
        ]):
            diff_oper = True

        #
        # Node > Ports
        #
        remain_ports = {}
        for port in cur_node["ports"]:
            remain_ports[port["id"]] = port
        for new_port in new_node["ports"]:
            cur_port = remain_ports.pop(new_port["id"], None)
            if not cur_port:
                diff_admin = True
                continue
            if any([
                new_port["mtu"] != cur_port["mtu"],
                new_port["nni"] != cur_port["nni"],
                new_port["services"] != cur_port["services"],
                new_port["state"] != cur_port["state"],
                new_port["type"] != cur_port["type"],
                new_port["private"] != cur_port["private"],
            ]):
                diff_admin = True
            if any([
                new_port["status"] != cur_port["status"],
            ]):
                diff_oper = True
        # ports removed
        if remain_ports:
            diff_admin = True
    # nodes removed
    if remain_nodes:
        diff_admin = True

    #
    # Links
    #
    remain_links = {}
    for link in cur_topo["links"]:
        remain_links[link["id"]] = link
    for new_link in new_topo["links"]:
        # link was added
        cur_link = remain_links.pop(new_link["id"], None)
        if not cur_link:
            diff_admin = True
            continue
        if any([
            new_link["bandwidth"] != cur_link["bandwidth"],
            new_link["ports"] != cur_link["ports"],
            new_link["state"] != cur_link["state"],
        ]):
            diff_admin = True
        if any([
            new_link["status"] != cur_link["status"],
        ]):
            diff_oper = True
    # links removed
    if remain_links:
        diff_admin = True

    return diff_admin, diff_oper


def parse_oess_circuit(circuit):
    """Convert a circuit from OESS to SDX format."""
    sdx_l2vpn = {}
    sdx_l2vpn["service_id"] = circuit["circuit_id"]
    sdx_l2vpn["name"] = circuit["description"].replace(NAME_PREFIX, "")
    # TODO: status and state, what about decom circuits? what if all interfaces go down?
    sdx_l2vpn["status"] = "up" if circuit["state"] == "active" else "down"
    sdx_l2vpn["state"] = "enabled" if circuit["state"] == "active" else "disabled"
    sdx_l2vpn["created_on"] = circuit["created_on"]
    sdx_l2vpn["last_modified_on"] = circuit["last_modified_on"]
    sdx_l2vpn["endpoints"] = [
        {
            "port_id": oess2sdx.get(circuit["endpoints"][0]["interface_id"], {}).get("id", "unknown"),
            "vlan": circuit["endpoints"][0]["tag"]
        },
        {
            "port_id": oess2sdx.get(circuit["endpoints"][1]["interface_id"], {}).get("id", "unknown"),
            "vlan": circuit["endpoints"][1]["tag"]
        },
    ]
    return sdx_l2vpn


@app.route("/", methods=["GET"])
def home():
    return jsonify({}), 204


@app.route("/topology/2.0.0", methods=["GET"])
def get_topology():
    global sdx_topology, sdx_topo_conv
    try:
        new_topo = get_oess_topo()
    except Exception as exc:
        err = traceback.format_exc().replace("\n", ", ")
        return jsonify({"result": "Failed to obtain topology from OESS: %s - %s" % (exc, err)}), 400
    sdx_topology = new_topo
    load_config()
    try:
        converted = convert_topo(sdx_topology)
    except Exception as exc:
        err = traceback.format_exc().replace("\n", ", ")
        app.logger.error(": %s - %s" % (exc, err))
        return jsonify({"result": "Failed to convert topology - Check admin logs"}), 400
    diff_admin, diff_oper = check_topo_diff(sdx_topo_conv, converted)
    if diff_admin or diff_oper:
        converted["timestamp"] = utcnow()
    if diff_admin:
        update_version()
        converted["version"] = sdx_version
    sdx_topo_conv = converted
    return jsonify(converted), 200


@app.route("/v1/l2vpn_ptp", methods=["POST"])
def create_l2vpn_ptp():
    content = request.get_json()

    # Sanity checks
    if not content:
        return jsonify({"result": "Create L2VPN failed - not a valid JSON payload"}), 400
    if "name" not in content:
        msg = "Create L2VPN failed -  missing attribute: name"
        return jsonify({"result": msg}), 400
    
    oess_params = [
        ("method", "provision"),
        ("workgroup_id", sdx_config["workgroup_id"]),
        ("provision_time", -1),
        ("remove_time", -1),
        ("circuit_id", -1),
        ("description", "%s%s" % (NAME_PREFIX, content["name"]))
    ]

    for uni_name in ["uni_a", "uni_z"]:
        intf = sdx2oess.get(content.get(uni_name, {}).get("port_id"))
        if not intf:
            return jsonify({"result": "Invalid/Missing L2VPN endpoint attribute %s" % (uni_name)}), 400
        vlan = content.get(uni_name, {}).get("tag", {}).get("value")
        if not isinstance(vlan, int):
            return jsonify({"result": "Invalid/Missing L2VPN endpoint vlan for %s" % (uni_name)}), 400
        # TODO: check if vlan is within the allowed range
        oess_params.append(
            ("endpoint", '{"tag": %s,"interface":"%s","node":"%s","bandwidth":0}' % (vlan, intf["name"], intf["node"]["name"]))
        )

    try:
        res = requests.post(sdx_config["oess_url"] + "/services/circuit.cgi", data=oess_params, verify=False, auth=(sdx_config["username"], sdx_config["password"]), timeout=timeout)
        assert res.status_code == 200, res.text
        assert "circuit_id" in res.json(), res.text
        circuit_id = res.json()["circuit_id"]
    except Exception as exc:
        msg = "Failed to create L2VPN on OESS: %s" % (exc)
        err = traceback.format_exc().replace("\n", ", ")
        app.logger.error(msg + " " + err)
        return jsonify({"result": msg}), 400

    return jsonify({"service_id": circuit_id}), 201

@app.route("/v1/l2vpn_ptp", methods=["DELETE"])
def delete_l2vpn_ptp():
    content = request.get_json()

    # Sanity check
    if not content:
        return jsonify({"result": "Create L2VPN failed - not a valid JSON payload"}), 400

    name = content.get("name")
    intf_id_0 = sdx2oess.get(content.get("uni_a", {}).get("interface_id"), {}).get("interface_id")
    intf_id_1 = sdx2oess.get(content.get("uni_z", {}).get("interface_id"), {}).get("interface_id")
    vlan_0 = content.get("uni_a", {}).get("tag", {}).get("value")
    vlan_1 = content.get("uni_z", {}).get("tag", {}).get("value")

    try:
        res = requests.get(sdx_config["oess_url"] + "/services/circuit.cgi?method=get&workgroup_id=%s" % (sdx_config["workgroup_id"]), verify=False, auth=(sdx_config["username"], sdx_config["password"]), timeout=timeout)
        assert res.status_code == 200, res.text
        data = res.json()
    except Exception as exc:
        msg = "Failed to DELETE L2VPN - Failed to get Circuits from OESS: %s" % (exc)
        err = traceback.format_exc().replace("\n", ", ")
        app.logger.error(msg + " " + err)
        return jsonify({"result": msg}), 400

    circuit_id = None
    for circuit in data["results"]:
        if all([
            circuit["description"] == NAME_PREFIX + str(name),
            len(circuit["endpoints"]) == 2,
            circuit["endpoints"][0]["interface_id"] == intf_id_0,
            circuit["endpoints"][1]["interface_id"] == intf_id_1,
            int(circuit["endpoints"][0]["tag"]) == vlan_0,
            int(circuit["endpoints"][1]["tag"]) == vlan_1,
        ]):
            circuit_id = circuit["circuit_id"]
            break

    if not circuit_id:
        return jsonify({"result": "Failed to DELETE L2VPN - Not found"}), 400

    try:
        res = requests.get(sdx_config["oess_url"] + "/services/circuit.cgi?method=remove&workgroup_id=%s&circuit_id=%s" % (sdx_config["workgroup_id"], circuit_id), verify=False, auth=(sdx_config["username"], sdx_config["password"]), timeout=timeout)
        assert res.status_code == 200, res.text
    except Exception as exc:
        msg = "Failed to delete L2VPN on OESS: %s" % (exc)
        err = traceback.format_exc().replace("\n", ", ")
        app.logger.error(msg + " " + err)
        return jsonify({"result": msg}), 400

    return jsonify({"result": "L2VPN deleted successfully"}), 200

@app.route("/l2vpn/1.0", methods=["POST"])
def create_l2vpn():
    content = request.get_json()

    # Sanity checks
    if not content:
        return jsonify({"result": "Create L2VPN failed - not a valid JSON payload"}), 400
    if "name" not in content:
        msg = "Create L2VPN failed -  missing attribute: name"
        return jsonify({"result": msg}), 400
    endpoints = content.get("endpoints", [])
    if len(endpoints) != 2:
        msg = "Create L2VPN failed - invalid list of endpoints: expected=2 was=%d" % (len(endpoints))
        return jsonify({"result": msg}), 400
    
    oess_params = [
        ("method", "provision"),
        ("workgroup_id", sdx_config["workgroup_id"]),
        ("provision_time", -1),
        ("remove_time", -1),
        ("circuit_id", -1),
        ("description", "%s%s" % (NAME_PREFIX, content["name"]))
    ]

    for endpoint in endpoints:
        if not isinstance(endpoint, dict):
            return jsonify({"result": "Invalid endpoint param format"}), 400
        port_id = endpoint.get("port_id")
        intf = sdx2oess.get(port_id)
        if not intf:
            return jsonify({"result": "Invalid endpoint - not found: %s" % (port_id)}), 400
        # TODO: check if vlan is within the allowed range
        vlan = endpoint.get("vlan")
        oess_params.append(
            ("endpoint", '{"tag": %s,"interface":"%s","node":"%s","bandwidth":0}' % (vlan, intf["name"], intf["node"]["name"]))
        )

    try:
        res = requests.post(sdx_config["oess_url"] + "/services/circuit.cgi", data=oess_params, verify=False, auth=(sdx_config["username"], sdx_config["password"]), timeout=timeout)
        assert res.status_code == 200, res.text
        assert "circuit_id" in res.json(), res.text
        circuit_id = res.json()["circuit_id"]
    except Exception as exc:
        msg = "Failed to create L2VPN on OESS: %s" % (exc)
        err = traceback.format_exc().replace("\n", ", ")
        app.logger.error(msg + " " + err)
        return jsonify({"result": msg}), 400

    return jsonify({"service_id": circuit_id}), 201

@app.route("/l2vpn/1.0/<int:service_id>", methods=["DELETE"])
def delete_l2vpn(service_id):
    try:
        res = requests.get(sdx_config["oess_url"] + "/services/circuit.cgi?method=remove&workgroup_id=%s&circuit_id=%s" % (sdx_config["workgroup_id"], service_id), verify=False, auth=(sdx_config["username"], sdx_config["password"]), timeout=timeout)
        assert res.status_code == 200, res.text
    except Exception as exc:
        msg = "Failed to delete L2VPN on OESS: %s" % (exc)
        err = traceback.format_exc().replace("\n", ", ")
        app.logger.error(msg + " " + err)
        return jsonify({"result": msg}), 400
    return jsonify({"result": "L2VPN deleted successfully"}), 200

@app.route("/l2vpn/1.0", methods=["GET"])
def get_all_l2vpn():
    try:
        res = requests.get(sdx_config["oess_url"] + "/services/circuit.cgi?method=get&workgroup_id=%s" % (sdx_config["workgroup_id"]), verify=False, auth=(sdx_config["username"], sdx_config["password"]), timeout=timeout)
        assert res.status_code == 200, res.text
        data = res.json()
    except Exception as exc:
        msg = "Failed to get L2VPN from OESS: %s" % (exc)
        err = traceback.format_exc().replace("\n", ", ")
        app.logger.error(msg + " " + err)
        return jsonify({"result": msg}), 400
    all_l2vpn = {}
    for result in data["results"]:
        if not result["description"].startswith(NAME_PREFIX):
            continue
        sdx_l2vpn = parse_oess_circuit(result)
        all_l2vpn[sdx_l2vpn["service_id"]] = sdx_l2vpn
    return jsonify(all_l2vpn), 200

@app.route("/l2vpn/1.0/<int:service_id>", methods=["GET"])
def get_l2vpn(service_id):
    try:
        res = requests.get(sdx_config["oess_url"] + "/services/circuit.cgi?method=get&workgroup_id=%s&circuit_id=%s" % (sdx_config["workgroup_id"], service_id), verify=False, auth=(sdx_config["username"], sdx_config["password"]), timeout=timeout)
        assert res.status_code == 200, res.text
        data = res.json()
        assert len(data.get("results", [])) == 1, "L2VPN service not found"
    except Exception as exc:
        msg = "Failed to get L2VPN from OESS: %s" % (exc)
        err = traceback.format_exc().replace("\n", ", ")
        app.logger.error(msg + err)
        return jsonify({"result": msg}), 400
    sdx_l2vpn = parse_oess_circuit(data["results"][0])
    return jsonify(sdx_l2vpn), 200

@app.route("/admin/oess2sdx", methods=["GET"])
def get_admin_map_oess2sdx():
    return jsonify(oess2sdx), 200

@app.route("/admin/sdx2oess", methods=["GET"])
def get_admin_map_sdx2oess():
    return jsonify(sdx2oess), 200

load_config(fallback_prev_config=False)
try:
    sdx_topology = get_oess_topo()
    converted = convert_topo(sdx_topology)
    diff_admin, diff_oper = check_topo_diff(sdx_topo_conv, converted)
    if diff_admin or diff_oper:
        converted["timestamp"] = utcnow()
    if diff_admin:
        update_version(0)
        converted["version"] = sdx_version
    sdx_topo_conv = converted
except Exception as exc:
    err = traceback.format_exc().replace("\n", ", ")
    app.logger.error("Failed to load topology: %s %s" % (exc, err))

if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port="8000")
