import streamlit as st
import pandas as pd
import geopandas as gpd
import folium
from shapely.geometry import Point
import osmnx as ox
import networkx as nx
from streamlit_folium import st_folium
import matplotlib.pyplot as plt
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import warnings
warnings.filterwarnings('ignore')

# Funzione per caricare il GeoJSON
@st.cache_data
def load_geojson(filepath):
    """Carica un file GeoJSON e lo converte in GeoDataFrame semplificato"""
    try:
        gdf = gpd.read_file(filepath)
        return gdf.simplify(tolerance=0.001)
    except Exception as e:
        st.error(f"Errore nel caricamento del GeoJSON: {str(e)}")
        return None

# Configurazione della cache per la rete stradale
@st.cache_data(ttl=3600)
def get_street_network(place, network_type):
    """Scarica e proietta la rete stradale solo per Milano"""
    try:
        with st.spinner('Scaricamento della rete stradale...'):
            G = ox.graph_from_place(place, network_type=network_type, simplify=True, retain_all=False)
            G = ox.project_graph(G)
            return G
    except Exception as e:
        st.error(f"Errore nel recupero della rete stradale: {str(e)}")
        return None

# Calcolo dell'isocrona ottimizzato e memorizzato in cache
@st.cache_data
def calculate_isochrone(G, center_point, max_dist):
    """Calcola e memorizza l'isocrona per un punto dato"""
    try:
        center_point_proj = ox.project_gdf(gpd.GeoSeries([center_point]), to_crs=G.graph['crs']).iloc[0]
        center_node = ox.nearest_nodes(G, center_point_proj.x, center_point_proj.y)
        subgraph = nx.ego_graph(G, center_node, radius=max_dist, distance='length')
        nodes, edges = ox.graph_to_gdfs(subgraph)
        isochrone = nodes.unary_union.convex_hull
        isochrone = ox.project_gdf(gpd.GeoSeries([isochrone]), to_crs='EPSG:4326').iloc[0]
        return isochrone
    except Exception as e:
        st.warning(f"Errore nel calcolo dell'isocrona: {str(e)}")
        return None

# Funzione per ottenere i servizi (POI) da OpenStreetMap
@st.cache_data
def get_amenities(place, tags):
    """Scarica i POI da OpenStreetMap in base ai tag specificati"""
    try:
        amenities = ox.geometries_from_place(place, tags)
        return amenities
    except Exception as e:
        st.warning(f"Errore nel recupero dei servizi: {str(e)}")
        return None

def calculate_connectivity_for_quarter(row, G, max_distance, poi):
    centroid = row.geometry.centroid
    isochrone = calculate_isochrone(G, centroid, max_distance)
    if isochrone is not None:
        services_in_area = poi[poi.intersects(isochrone)]
        return len(services_in_area)
    else:
        return 0

def main():
    st.set_page_config(
        page_title="Analisi Connettività Milano",
        page_icon="🏙️",
        layout="wide"
    )

    st.title('Analisi della Connettività dei Quartieri di Milano')

    # Barra laterale per i parametri
    with st.sidebar:
        st.header('Parametri di Analisi')
        
        available_services = ['supermarket', 'gym', 'school', 'hospital', 'pharmacy']
        selected_services = st.multiselect(
            'Seleziona i servizi primari di interesse:',
            available_services,
            default=['supermarket', 'pharmacy']
        )

        transport_mode = st.selectbox(
            'Modalità di trasporto:',
            ['A piedi', 'In auto']
        )

        network_type = 'walk' if transport_mode == 'A piedi' else 'drive'
        speed = 5 if transport_mode == 'A piedi' else 40  # km/h

        max_time = st.slider(
            'Tempo massimo di viaggio (minuti):',
            min_value=5, max_value=60, value=15, step=5
        )

        show_services = st.checkbox('Mostra i servizi sulla mappa')

    # Caricamento dati
    try:
        quartieri = load_geojson('quartieri_milano.geojson')
        if quartieri is None or not isinstance(quartieri, gpd.GeoDataFrame):
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

        # Calcolo connettività ottimizzato
        with st.spinner('Calcolo della connettività in corso...'):
            speed_m_per_sec = speed * 1000 / 3600
            max_distance = speed_m_per_sec * max_time * 60

            with ThreadPoolExecutor() as executor:
                connectivity_scores = list(executor.map(
                    lambda row: calculate_connectivity_for_quarter(row[1], G, max_distance, poi),
                    quartieri.iterrows()
                ))

            quartieri['connettività'] = connectivity_scores
            quartieri['punteggio_norm'] = quartieri['connettività'] / quartieri['connettività'].max()

        # Visualizzazione risultati
        col1, col2 = st.columns([2, 1])

        with col1:
            m = folium.Map(location=[45.4642, 9.19], zoom_start=12)
            choropleth = folium.Choropleth(
                geo_data=quartieri,
                name='Connettività',
                data=quartieri,
                columns=['NIL', 'punteggio_norm'],
                key_on='feature.properties.NIL',
                fill_color='YlGn',
                fill_opacity=0.7,
                line_opacity=0.2,
                legend_name='Punteggio di Connettività'
            ).add_to(m)

            folium.GeoJsonTooltip(
                fields=['NIL', 'connettività'],
                aliases=['Quartiere', 'Punteggio'],
                style=('background-color: white; color: #333333; font-family: arial; font-size: 12px; padding: 10px;')
            ).add_to(choropleth.geojson)

            if show_services:
                for idx, row in poi.iterrows():
                    geom = row.geometry
                    if isinstance(geom, Point):
                        folium.CircleMarker(
                            location=[geom.y, geom.x],
                            radius=2,
                            color='blue',
                            fill=True,
                            fill_color='blue'
                        ).add_to(m)

            st_folium(m, width=800, height=600)

        with col2:
            st.header('Statistiche')
            st.write(f"Numero totale di servizi: {len(poi)}")
            st.write(f"Punteggio medio: {quartieri['connettività'].mean():.2f}")
            st.write(f"Punteggio massimo: {quartieri['connettività'].max():.0f}")
            
            st.subheader('Top 10 Quartieri')
            top_10 = quartieri.sort_values(by='connettività', ascending=False)[['NIL', 'connettività']].head(10)
            st.dataframe(top_10)

    except Exception as e:
        st.error(f"Si è verificato un errore: {str(e)}")

if __name__ == "__main__":
    main()
