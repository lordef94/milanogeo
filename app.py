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
import pickle
import warnings
warnings.filterwarnings('ignore')

def load_geojson(filepath):
    """Carica un file GeoJSON e lo converte in GeoDataFrame"""
    try:
        return gpd.read_file(filepath)
    except Exception as e:
        st.error(f"Errore nel caricamento del GeoJSON: {str(e)}")
        return None

def get_cache_dir():
    """Crea e restituisce il percorso della directory cache"""
    script_dir = Path(__file__).parent.absolute()
    cache_dir = script_dir / 'cache'
    cache_dir.mkdir(exist_ok=True)
    return cache_dir

def get_network_cache_path(network_type):
    """Restituisce il percorso completo del file cache per il tipo di rete specificato"""
    cache_dir = get_cache_dir()
    return cache_dir / f'network_cache_{network_type}.pkl'

def save_network_to_cache(G, network_type):
    """Salva la rete stradale in un file cache"""
    try:
        cache_path = get_network_cache_path(network_type)
        with open(cache_path, 'wb') as f:
            pickle.dump(G, f)
    except Exception as e:
        st.warning(f"Impossibile salvare la cache: {str(e)}")

def load_network_from_cache(network_type):
    """Carica la rete stradale dalla cache"""
    try:
        cache_path = get_network_cache_path(network_type)
        if cache_path.exists():
            with open(cache_path, 'rb') as f:
                return pickle.load(f)
    except Exception:
        return None
    return None

@st.cache_data(ttl=3600)
def get_amenities(place, tags):
    """Scarica i POI da OpenStreetMap in base ai tag specificati"""
    try:
        amenities = ox.geometries_from_place(place, tags)
        return amenities
    except Exception as e:
        st.warning(f"Errore nel recupero dei servizi: {str(e)}")
        return None

@st.cache_resource
def get_street_network(place, network_type):
    """Scarica e proietta la rete stradale con gestione della cache"""
    G = load_network_from_cache(network_type)
    
    if G is None:
        try:
            with st.spinner('Scaricamento della rete stradale...'):
                G = ox.graph_from_place(place, network_type=network_type)
                G = ox.project_graph(G)
                save_network_to_cache(G, network_type)
        except Exception as e:
            st.error(f"Errore nel recupero della rete stradale: {str(e)}")
            return None
    
    return G

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

def create_map(quartieri, poi, show_services):
    """Crea la mappa con choropleth e servizi"""
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

    return m

def main():
    st.set_page_config(
        page_title="Analisi Connettività Milano",
        page_icon="🏙️",
        layout="wide"
    )

    st.title('Analisi della Connettività dei Quartieri di Milano')

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
        speed = 5 if transport_mode == 'A piedi' else 40

        max_time = st.slider(
            'Tempo massimo di viaggio (minuti):',
            min_value=5, max_value=60, value=15, step=5
        )

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

        speed_m_per_sec = speed * 1000 / 3600
        max_distance = speed_m_per_sec * max_time * 60

        with st.spinner('Calcolo della connettività in corso...'):
            connectivity_scores = calculate_connectivity_scores(quartieri, G, poi, max_distance)
            quartieri['connettività'] = connectivity_scores
            quartieri['punteggio_norm'] = quartieri['connettività'] / quartieri['connettività'].max()

        col1, col2 = st.columns([2, 1])

        with col1:
            m = create_map(quartieri, poi, show_services)
            st_folium(m, width=800, height=600)
            
            st.write("### Metodo di Calcolo del Punteggio di Connettività")
            st.write("""
                Il punteggio di connettività di ciascun quartiere è calcolato considerando il numero di servizi accessibili
                entro un'isocrona, ovvero un'area raggiungibile entro un certo tempo di viaggio (in minuti). La distanza massima
                raggiungibile è calcolata in funzione della velocità (5 km/h per camminare, 40 km/h per l'auto) moltiplicata
                per il tempo selezionato dall'utente. 
            """)
        
            st.write("### Fonti dei Dati")
            st.write("""
                - **OpenStreetMap**: utilizzato per i dati di rete stradale e per individuare i punti di interesse (POI).
                - **Geopandas e Folium**: utilizzati per la gestione dei dati geografici e la visualizzazione su mappa.
                - **OSMnx**: utilizzato per scaricare e proiettare la rete stradale, calcolando le isocrone di connettività.
            """)

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
