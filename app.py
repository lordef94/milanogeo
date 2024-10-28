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
import requests
from bs4 import BeautifulSoup
warnings.filterwarnings('ignore')

def load_geojson(filepath):
    try:
        return gpd.read_file(filepath)
    except Exception as e:
        st.error(f"Errore nel caricamento del GeoJSON: {str(e)}")
        return None

def get_cache_dir():
    script_dir = Path(__file__).parent.absolute()
    cache_dir = script_dir / 'cache'
    cache_dir.mkdir(exist_ok=True)
    return cache_dir

def get_network_cache_path(network_type):
    cache_dir = get_cache_dir()
    return cache_dir / f'network_cache_{network_type}.pkl'

def save_network_to_cache(G, network_type):
    try:
        cache_path = get_network_cache_path(network_type)
        with open(cache_path, 'wb') as f:
            pickle.dump(G, f)
    except Exception as e:
        st.warning(f"Impossibile salvare la cache: {str(e)}")

def load_network_from_cache(network_type):
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
    try:
        amenities = ox.geometries_from_place(place, tags)
        return amenities
    except Exception as e:
        st.warning(f"Errore nel recupero dei servizi: {str(e)}")
        return None

@st.cache_resource
def get_street_network(place, network_type):
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

def calculate_isochrone(G, center_point, max_dist):
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

@st.cache_data
def calculate_connectivity_scores(_quartieri_json, _poi_json, _G, max_distance):
    try:
        quartieri = gpd.GeoDataFrame.from_features(_quartieri_json)
        poi = gpd.GeoDataFrame.from_features(_poi_json)
        
        connectivity_scores = []
        
        for idx, row in quartieri.iterrows():
            centroid = row.geometry.centroid
            try:
                isochrone = calculate_isochrone(_G, centroid, max_distance)
                if isochrone is not None:
                    services_in_area = poi[poi.intersects(isochrone)]
                    score = len(services_in_area)
                else:
                    score = 0
            except Exception as e:
                st.warning(f"Errore nel calcolo del punteggio per il quartiere {row['NIL']}: {str(e)}")
                score = 0
            connectivity_scores.append(score)
        
        return connectivity_scores
    except Exception as e:
        st.error(f"Errore nel calcolo dei punteggi di connettività: {str(e)}")
        return None

def scrape_real_estate_prices(place):
    try:
        url = f"https://www.immobiliare.it/vendita-case/{place.lower().replace(' ', '-')}/"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/85.0.4183.121 Safari/537.36"
        }
        response = requests.get(url, headers=headers)
        if response.status_code != 200:
            st.warning(f"Errore nella richiesta a Immobiliare.it: {response.status_code}")
            return None

        soup = BeautifulSoup(response.content, "html.parser")
        prices = []
        for price_tag in soup.find_all("li", class_="nd-list__item--main-info"):
            price = price_tag.find("span", class_="nd-list__item-price")
            if price:
                prices.append(price.get_text(strip=True))

        return prices
    except Exception as e:
        st.warning(f"Errore durante lo scraping dei prezzi immobiliari: {str(e)}")
        return None

def create_map(quartieri, poi, show_services):
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

    if show_services and not poi.empty:
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
        page_icon="🌇",
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
            quartieri_json = quartieri.__geo_interface__
            poi_json = poi.__geo_interface__
            
            connectivity_scores = calculate_connectivity_scores(quartieri_json, poi_json, G, max_distance)
            if connectivity_scores is not None:
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

        st.header('Classifica Completa dei Quartieri')
        
        full_rankings = quartieri[['NIL', 'connettività']].copy()
        full_rankings = full_rankings.sort_values(by='connettività', ascending=False)
        full_rankings.columns = ['Quartiere', 'Punteggio di Connettività']
        full_rankings.index = range(1, len(full_rankings) + 1)
        
        col_filter1, col_filter2 = st.columns([1, 2])
        
        with col_filter1:
            search_term = st.text_input('Cerca quartiere:', '')
            
        with col_filter2:
            score_range = st.slider(
                'Filtra per punteggio:',
                min_value=float(full_rankings['Punteggio di Connettività'].min()),
                max_value=float(full_rankings['Punteggio di Connettività'].max()),
                value=(float(full_rankings['Punteggio di Connettività'].min()),
                      float(full_rankings['Punteggio di Connettività'].max()))
            )
        
        mask = (full_rankings['Punteggio di Connettività'].between(score_range[0], score_range[1]))
        if search_term:
            mask &= full_rankings['Quartiere'].str.contains(search_term, case=False)
        
        filtered_rankings = full_rankings[mask]
        
        st.dataframe(
            filtered_rankings,
            column_config={
                "Quartiere": st.column_config.TextColumn(
                    "Quartiere",
                    width="medium"
                ),
                "Punteggio di Connettività": st.column_config.NumberColumn(
                    "Punteggio di Connettività",
                    format="%.2f",
                    width="small"
                )
            },
            hide_index=False,
            width=800
        )
        
        csv = filtered_rankings.to_csv(index=True)
        st.download_button(
            label="Scarica dati come CSV",
            data=csv,
            file_name="classifica_quartieri_milano.csv",
            mime="text/csv",
        )

        st.header('Prezzi degli Immobili')
        st.write('Recupero dei prezzi degli immobili in corso...')
        real_estate_prices = scrape_real_estate_prices('milano')
        if real_estate_prices:
            st.write(f"Numero di annunci trovati: {len(real_estate_prices)}")
            for price in real_estate_prices:
                st.write(price)
        else:
            st.write("Nessun prezzo trovato.")

    except Exception as e:
        st.error(f"Si è verificato un errore: {str(e)}")

if __name__ == "__main__":
    main()
