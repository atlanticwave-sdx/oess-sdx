#########
Changelog
#########
All notable changes to the OESS-SDX application will be documented in this file.

[UNRELEASED] - Under development
********************************

General Information
===================
-

Added
=====
-

Changed
=======
-

Fixed
=====


[3.2.0] - 2025-12-01
********************

Added
=====
- Introduction of the **oess-sdx** application for integrating AtlanticWave-SDX capabilities into OESS-MPLS (https://github.com/GlobalNOC/OESS/tree/2.0.17).
- Topology export from OESS-MPLS to SDX-LC using the official Topology Data Model.
- Support for provisioning L2VPN point-to-point services using SDX-LC as orchestrator.
- “New” L2VPN API (`/l2vpn/1.0`) with resources to create, edit, list, and delete services.
- “Legacy” provisioning API (`/v1/l2vpn_ptp`) maintained for compatibility.
- Support for `entities` into SDX Port objects.
- Support for flexible VLAN configurations using interface metadata `sdx_vlan_range`.
- Support for marking interfaces as NNI links through `sdx_nni` metadata for multi-domain topologies.

Changed
=======

- Updated Dockerfile and development workflow for compatibility with Kytos-ng.

Fixed
=====

- Fixed an error when exporting the mapping from sdx2oess for interfaces which leads to loop serialization error
- Various internal improvements and bug fixes in topology export and L2VPN provisioning logic.
- Enhanced validation for interface metadata and VLAN range inputs.
- Avoid blocking setup() when loading topology
