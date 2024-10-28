@st.cache_data
def calculate_connectivity_scores(_quartieri, _G, _poi, max_distance):
    """Calcola i punteggi di connettività per tutti i quartieri"""
    connectivity_scores = []
    
    for idx, row in _quartieri.iterrows():
        centroid = row.geometry.centroid
        try:
            isochrone = calculate_isochrone(_G, centroid, max_distance)
            if isochrone is not None:
                services_in_area = _poi[_poi.intersects(isochrone)]
                score = len(services_in_area)
            else:
                score = 0
        except Exception as e:
            st.warning(f"Errore nel calcolo del punteggio per il quartiere {row['NIL']}: {str(e)}")
            score = 0
        connectivity_scores.append(score)
    
    return connectivity_scores

@st.cache_data
def calculate_isochrone(_G, center_point, max_dist):
    """Calcola l'isocrona per un punto dato"""
    try:
        center_point_proj = ox.projection.project_geometry(center_point, to_crs=_G.graph['crs'])[0]
        center_node = ox.nearest_nodes(_G, center_point_proj.x, center_point_proj.y)
        subgraph = nx.ego_graph(_G, center_node, radius=max_dist, distance='length')
        nodes, edges = ox.graph_to_gdfs(subgraph)
        isochrone = nodes.unary_union.convex_hull
        isochrone = ox.projection.project_geometry(isochrone, _G.graph['crs'], to_crs='EPSG:4326')[0]
        return isochrone
    except Exception as e:
        st.warning(f"Errore nel calcolo dell'isocrona: {str(e)}")
        return None
