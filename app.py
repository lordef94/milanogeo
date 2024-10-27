import streamlit as st
import pandas as pd
import geopandas as gpd
import folium
from shapely.geometry import Point
import osmnx as ox
import networkx as nx
from streamlit_folium import st_folium
import matplotlib.pyplot as plt
from pathlib import Path
import warnings
warnings.filterwarnings('ignore')

# Define a cache dictionary for isochrone and POI intersections
isochrone_cache = {}

def load_geojson(filepath):
    """Carica un file GeoJSON e lo converte in GeoDataFrame"""
    try:
        return gpd.read_file(filepath)
    except Exception as e:
        st.error(f"Errore nel caricamento del GeoJSON: {str(e)}")
        return None

def get_amenities(place, tags):
    """Scarica i POI da OpenStreetMap in base ai tag specificati"""
    try:
        amenities = ox.geometries_from_place(place, tags)
        return amenities
    except Exception as e:
        st.warning(f"Errore nel recupero dei servizi: {str(e)}")
        return None

@st.cache_data(ttl=3600)
def get_street_network(place, network_type):
    """Scarica e proietta la rete stradale"""
    try:
        with st.spinner('Scaricamento della rete stradale...'):
            G = ox.graph_from_place(place, network_type=network_type)
            G = ox.project_graph(G)
            return G
    except Exception as e:
        st.error(f"Errore nel recupero della rete stradale: {str(e)}")
        return None

def calculate_isochrone(G, center_point, max_dist):
    """Calcola l'isocrona per un punto dato"""
    try:
        center_point_proj = ox.projection.project_geometry(center_point, to_crs=G.graph['crs'])[0]
        center_node = ox.nearest_nodes(G, center_point_proj.x, center_point_proj.y)
        subgraph = nx.ego_graph(G, center_node, radius=max_dist, distance='length')
        nodes, edges = ox.graph_to_gdfs(subgraph)
        isochrone = nodes.unary_union.convex_hull
        isochrone = ox.projection.project_geometry(isochrone, G.graph['crs'], to_crs='EPSG:4326')[0]
        return isochrone
    except Exception as e:
        st.warning(f"Errore nel calcolo dell'isocrona: {str(e)}")
        return None

def calculate_connectivity_score(row, G, poi, max_distance):
    """Calculate the connectivity score with caching."""
    # Check if result is already cached for this neighborhood
    if row['NIL'] in isochrone_cache:
        return isochrone_cache[row['NIL']]
    
    centroid = row.geometry.centroid
    isochrone = calculate_isochrone(G, centroid, max_distance)
    
    if isochrone is not None:
        # Calculate and cache the number of POIs within the isochrone area
        services_in_area = poi[poi.intersects(isochrone)]
        score = len(services_in_area)
    else:
        score = 0
    
    # Cache the result
    isochrone_cache[row['NIL']] = score
    return score

def main():
    st.set_page_config(page_title="Analisi Connettività Milano", page_icon="🏙️", layout="wide")
    st.title('Analisi della Connettività dei Quartieri di Milano')

    with st.sidebar:
        st.header('Parametri di Analisi')
        available_services = ['supermarket', 'gym', 'school', 'hospital', 'pharmacy']
        selected_services = st.multiselect('Seleziona i servizi primari di interesse:', available_services, default=['supermarket', 'pharmacy'])
        transport_mode = st.selectbox('Modalità di trasporto:', ['A piedi', 'In auto'])
        network_type = 'walk' if transport_mode == 'A piedi' else 'drive'
        speed = 5 if transport_mode == 'A piedi' else 40
        max_time = st.slider('Tempo massimo di viaggio (minuti):', min_value=5, max_value=60, value=15, step=5)
        show_services = st.checkbox('Mostra i servizi sulla mappa')

    try:
        quartieri = load_geojson('quartieri_milano.geojson')
        if quartieri is None:
            st.error("Impossibile procedere senza i dati dei quartieri")
            return
        G = get_street_network('Milano, Italia', network_type)
        if G is None:
            st.error("Impossibile procedere senza la rete stradale")
            return
        tags = {'amenity': selected_services}
        poi = get_amenities('Milano, Italia', tags)
        if poi is None:
            st.error("Impossibile procedere senza i dati dei servizi")
            return

        with st.spinner('Calcolo della connettività in corso...'):
            speed_m_per_sec = speed * 1000 / 3600
            max_distance = speed_m_per_sec * max_time * 60
            connectivity_scores = [calculate_connectivity_score(row, G, poi, max_distance) for _, row in quartieri.iterrows()]

            quartieri['connettività'] = connectivity_scores
            quartieri['punteggio_norm'] = quartieri['connettività'] / max(connectivity_scores)

        # Mapping and display code goes here...

    except Exception as e:
        st.error(f"Si è verificato un errore: {str(e)}")

if __name__ == "__main__":
    main()
