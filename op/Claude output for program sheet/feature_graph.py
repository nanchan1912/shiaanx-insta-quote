"""
feature_graph.py
----------------
Builds a NetworkX graph of faces from the PythonOCC JSON output.

Each node  = one face, with all its geometry attributes attached.
Each edge  = two faces share a physical edge in the BRep topology
             (taken directly from the face_adjacency block in the JSON).

This graph is the input to the clustering algorithm in cluster_features.py.
"""

import networkx as nx


def build_face_graph(json_data):
    """
    Build and return a NetworkX Graph from the PythonOCC feature JSON.

    Parameters
    ----------
    json_data : dict
        The full parsed JSON output from the PythonOCC extraction script.
        Must contain:
          - json_data['faces']['faces']   : list of face dicts
          - json_data['face_adjacency']   : dict of {str(face_idx): [neighbour_idxs]}

    Returns
    -------
    G : nx.Graph
        Nodes are integer face indices (0-based, matching JSON order).
        Node attributes mirror the face dict fields exactly:
            G.nodes[i]['surface_type']  -> 'Cylinder' or 'Plane'
            G.nodes[i]['area']          -> float
            G.nodes[i]['orientation']   -> 'Forward' or 'Reversed'
            G.nodes[i]['cylinder']      -> dict (only if surface_type == 'Cylinder')
            G.nodes[i]['plane']         -> dict (only if surface_type == 'Plane')
        Edges carry no attributes — presence of an edge means shared BRep edge.
    """
    faces      = json_data['faces']['faces']
    adjacency  = json_data['face_adjacency']

    G = nx.Graph()

    # --- Add nodes ---
    for i, face in enumerate(faces):
        # Store all face attributes directly on the node
        # so downstream code can read G.nodes[i]['surface_type'] etc.
        G.add_node(i, **face)

    # --- Add edges from adjacency ---
    # Keys in face_adjacency are strings ("0", "1", ...) — convert to int
    for face_idx_str, neighbours in adjacency.items():
        face_idx = int(face_idx_str)
        for neighbour_idx in neighbours:
            # add_edge is idempotent — adding the same edge twice is safe
            G.add_edge(face_idx, neighbour_idx)

    return G


def graph_summary(G):
    """
    Print a readable summary of the face graph.
    Useful for debugging and validating the graph was built correctly.
    """
    cylinders = [n for n, d in G.nodes(data=True) if d['surface_type'] == 'Cylinder']
    planes    = [n for n, d in G.nodes(data=True) if d['surface_type'] == 'Plane']

    print(f"Face graph: {G.number_of_nodes()} nodes, {G.number_of_edges()} edges")
    print(f"  Cylinder faces : {len(cylinders)}  {cylinders}")
    print(f"  Plane faces    : {len(planes)}  {planes}")
    print()

    print("Adjacency list:")
    for node in sorted(G.nodes()):
        neighbours = sorted(G.neighbors(node))
        stype = G.nodes[node]['surface_type']
        orient = G.nodes[node]['orientation']
        print(f"  Face {node:3d} [{stype:8s} / {orient:8s}] -> {neighbours}")
