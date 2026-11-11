import json
import os
import re

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

st.set_page_config(
    page_title="UFBA Road - Matriz Curricular & Mapa",
    layout="wide",
    initial_sidebar_state="collapsed"
)

# Oculta menus e paddings nativos do Streamlit para ocupar 100% da tela
st.markdown("""
<style>
    header, footer, #MainMenu { visibility: hidden !important; height: 0 !important; display: none !important; }
    .block-container {
        padding: 0 !important;
        margin: 0 !important;
        max-width: 100vw !important;
        overflow: hidden !important;
    }
    div[data-testid="stToolbar"] { display: none !important; }
    div[data-testid="stDecoration"] { display: none !important; }
    iframe { border: none !important; width: 100vw !important; height: 100vh !important; }
    html, body, [data-testid="stAppViewContainer"] {
        overflow: hidden !important;
        height: 100vh !important;
        background-color: #0b0f19 !important;
    }
</style>
""", unsafe_allow_html=True)

# ==============================================================================
# CONFIGURAÇÃO: CAMINHO DA PASTA DOS DADOS
# ==============================================================================
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent

DISCIPLINAS_CSV = DATA_DIR / "disciplinas.csv"
CURSO_DISCIPLINA_CSV = DATA_DIR / "curso_disciplina.csv"
EXPRESSOES_CSV = DATA_DIR / "disciplinas_unidades_prerequisitos.csv"
CURSO_INSTITUTO_CSV = DATA_DIR / "curso_instituto.csv"
PROBLEMAS_CSV = DATA_DIR / "problemas_dados.csv"
UNIDADES_GEO_CSV = DATA_DIR / "unidades_para_georreferenciar.csv"

@st.cache_data
def ler_csv(caminho):
    if not os.path.exists(caminho):
        return pd.DataFrame()
    try:
        return pd.read_csv(caminho, dtype=str, encoding="utf-8-sig").fillna("")
    except Exception:
        try:
            return pd.read_csv(caminho, dtype=str, encoding="latin1").fillna("")
        except Exception as e:
            st.error(f"Erro ao ler {caminho}: {e}")
            return pd.DataFrame()


PADRAO_TOKEN = r"\(|\)|\bOU\b|\bE\b|[A-Z]+\d+"


def forma_normal(expressao):
    expressao = expressao.strip().upper()
    tokens = re.findall(PADRAO_TOKEN, expressao)
    if "".join(tokens) != re.sub(r"\s+", "", expressao):
        resto = re.sub(PADRAO_TOKEN + r"|\s+", "", expressao)
        if resto:
            raise ValueError(f"caracteres nao reconhecidos: {resto!r}")
        raise ValueError("caracteres nao reconhecidos")
    pos = 0

    def ler_ou():
        nonlocal pos
        grupos = ler_e()
        while pos < len(tokens) and tokens[pos] == "OU":
            pos += 1
            grupos += ler_e()
        return grupos

    def ler_e():
        nonlocal pos
        grupos = ler_item()
        while pos < len(tokens) and tokens[pos] == "E":
            pos += 1
            direita = ler_item()
            grupos = [a | b for a in grupos for b in direita]
        return grupos

    def ler_item():
        nonlocal pos
        if pos >= len(tokens):
            raise ValueError("expressao incompleta")
        token = tokens[pos]
        pos += 1
        if token == "(":
            grupos = ler_ou()
            if pos >= len(tokens) or tokens[pos] != ")":
                raise ValueError("parenteses nao fechados")
            pos += 1
            return grupos
        if token in ("E", "OU", ")"):
            raise ValueError(f"token inesperado: {token}")
        return [frozenset([token])]

    grupos = set(ler_ou())
    if pos != len(tokens):
        raise ValueError("tokens nao processados")

    minimos = [g for g in grupos if not any(outro < g for outro in grupos)]
    return [sorted(g) for g in sorted(minimos, key=lambda g: (len(g), sorted(g)))]


@st.cache_data
def carregar_dados():
    disciplinas = ler_csv(DISCIPLINAS_CSV)
    curso_disciplina = ler_csv(CURSO_DISCIPLINA_CSV)
    expressoes = ler_csv(EXPRESSOES_CSV)
    curso_instituto = ler_csv(CURSO_INSTITUTO_CSV)

    if disciplinas.empty or not {"id", "nome"}.issubset(disciplinas.columns):
        return None, None, None, None, [], pd.DataFrame(), pd.DataFrame()

    for tabela in (disciplinas, curso_disciplina, expressoes):
        for coluna in tabela.columns:
            tabela[coluna] = tabela[coluna].astype(str).str.strip()

    problemas = []

    def registrar(tipo, disciplina, curso, detalhe):
        problemas.append({
            "tipo": tipo,
            "disciplina": disciplina,
            "curso": curso,
            "detalhe": detalhe,
        })

    nomes = dict(zip(disciplinas["id"], disciplinas["nome"]))
    cargas = {}
    for _, linha in disciplinas.iterrows():
        try:
            cargas[linha["id"]] = float(linha.get("carga_horaria", "0") or 0)
        except ValueError:
            cargas[linha["id"]] = 0.0

    ofertas = {}
    if not curso_disciplina.empty:
        for coluna in ("disciplina_id", "curso", "tipo", "periodo"):
            if coluna not in curso_disciplina.columns:
                curso_disciplina[coluna] = ""
        for _, linha in curso_disciplina.iterrows():
            if linha["disciplina_id"]:
                ofertas.setdefault(linha["disciplina_id"], []).append({
                    "curso": linha["curso"],
                    "tipo": linha["tipo"],
                    "periodo": linha["periodo"],
                })

    for disc_id, regs in ofertas.items():
        if disc_id not in nomes:
            cursos = ", ".join(sorted({r["curso"] for r in regs if r["curso"]}))
            registrar("OFERTA_SEM_DISCIPLINA", disc_id, cursos,
                      "aparece em curso_disciplina.csv mas nao existe em disciplinas.csv")

    pares_ofertados = {(d, r["curso"]) for d, regs in ofertas.items() for r in regs}

    exp_map = {}
    for _, linha in expressoes.iterrows():
        texto = linha.get("expressao_pre_requisitos", "")
        if not texto:
            continue
        disc = linha.get("disciplina_id", "")
        curso = linha.get("curso", "")
        chave = (disc, curso)

        erro = ""
        try:
            grupos = forma_normal(texto)
        except Exception as exc:
            grupos = []
            erro = str(exc)
            registrar("EXPRESSAO_INVALIDA", disc, curso, f"{erro} | expressao: {texto}")

        if chave in exp_map:
            registrar("EXPRESSAO_DUPLICADA", disc, curso,
                      "mais de uma expressao para a mesma disciplina/curso; a ultima prevaleceu")

        if chave not in pares_ofertados:
            registrar("EXPRESSAO_SEM_OFERTA", disc, curso,
                      f"a disciplina nao e ofertada nesse curso em curso_disciplina.csv | expressao: {texto}")

        membros = sorted({m for g in grupos for m in g})
        for membro in membros:
            if membro not in nomes:
                registrar("REFERENCIA_INEXISTENTE", disc, curso,
                          f"pre-requisito {membro} nao existe em disciplinas.csv")
        if disc in membros:
            registrar("AUTO_REFERENCIA", disc, curso, "a disciplina e pre-requisito de si mesma")

        exp_map[chave] = {"texto": texto, "grupos": grupos, "erro": erro}

    return nomes, cargas, ofertas, exp_map, problemas, curso_disciplina, curso_instituto


nomes, cargas, ofertas, exp_map, problemas, curso_disciplina, curso_instituto = carregar_dados()

if not nomes:
    st.error(f"⚠️ Erro ao carregar dados da pasta: {DATA_DIR}")
    st.info("Verifique se os arquivos `disciplinas.csv`, `curso_disciplina.csv` e `disciplinas_unidades_prerequisitos.csv` estão no caminho configurado no topo do código.")
    st.stop()

try:
    pd.DataFrame(problemas, columns=["tipo", "disciplina", "curso", "detalhe"]).to_csv(
        PROBLEMAS_CSV, index=False, encoding="utf-8"
    )
except OSError:
    pass

# Carrega unidades para o filtro inicial (usando a coluna 'instituto' de curso_instituto.csv)
if not curso_instituto.empty and "instituto" in curso_instituto.columns:
    lista_unidades = sorted(list({str(i).strip() for i in curso_instituto["instituto"] if str(i).strip()}))
else:
    lista_unidades = []

lista_cursos_geral = sorted({item["curso"] for regs in ofertas.values() for item in regs if item["curso"]})

# Gerenciamento de Estado da Tela Inicial
if "explorando" not in st.session_state:
    st.session_state["explorando"] = False
if "filtro_unidade_inicial" not in st.session_state:
    st.session_state["filtro_unidade_inicial"] = "TODAS"
if "filtro_curso_inicial" not in st.session_state:
    st.session_state["filtro_curso_inicial"] = "TODOS"

if not st.session_state["explorando"]:
    # Exibe a tela inicial com o design exato solicitado
    st.markdown("""
    <style>
        .stApp {
            background-color: #0b0f19 !important;
        }
        .welcome-container {
            display: flex;
            justify-content: center;
            align-items: center;
            height: 100vh;
            width: 100vw;
            background: radial-gradient(circle at center, #111827 0%, #0b0f19 100%);
            position: fixed;
            top: 0;
            left: 0;
            z-index: 999;
        }
        .welcome-card {
            background: rgba(15, 23, 42, 0.85);
            backdrop-filter: blur(20px);
            border: 1px solid rgba(255, 255, 255, 0.12);
            box-shadow: 0 20px 50px rgba(0, 0, 0, 0.8), 0 0 40px rgba(168, 85, 247, 0.15);
            border-radius: 20px;
            padding: 40px;
            width: 480px;
            max-width: 90vw;
        }
        .welcome-label {
            font-size: 11px;
            font-weight: 700;
            letter-spacing: 0.08em;
            text-transform: uppercase;
            margin-bottom: 8px;
        }
        .label-unidade { color: #38bdf8; }
        .label-curso { color: #f59e0b; margin-top: 20px; }
    </style>
    """, unsafe_allow_html=True)

    # Conteúdo centralizado via Streamlit columns/container
    col1, col2, col3 = st.columns([1, 2, 1])
    with col2:
        st.markdown("<div style='font-size: 11px; font-weight: 700; color: #38bdf8; text-transform: uppercase; letter-spacing: 0.08em; margin-bottom: 6px;'>Unidade</div>", unsafe_allow_html=True)
        unidade_escolhida = st.selectbox(
            "Selecione a unidade...",
            options=["Todas as unidades..."] + lista_unidades,
            label_visibility="collapsed",
            key="sb_unidade"
        )

        # Filtra os cursos com base na unidade escolhida na tela inicial
        if unidade_escolhida != "Todas as unidades...":
            cursos_filtrados_unidade = sorted(list({
                str(row.get("curso", "")).strip()
                for _, row in curso_instituto.iterrows()
                if str(row.get("instituto", "")).strip().lower() == unidade_escolhida.strip().lower() 
                or unidade_escolhida.strip().lower() in str(row.get("instituto", "")).strip().lower()
            }))
            # Garante que apareçam apenas os cursos válidos/gerais que estão nessa unidade
            lista_cursos_modal = [c for c in lista_cursos_geral if c in cursos_filtrados_unidade]
            if not lista_cursos_modal:
                lista_cursos_modal = cursos_filtrados_unidade
        else:
            lista_cursos_modal = lista_cursos_geral

        st.markdown("<div style='font-size: 11px; font-weight: 700; color: #f59e0b; text-transform: uppercase; letter-spacing: 0.08em; margin-top: 16px; margin-bottom: 6px;'>Curso</div>", unsafe_allow_html=True)
        curso_escolhido = st.selectbox(
            "Selecione o curso...",
            options=["Todos os cursos..."] + lista_cursos_modal,
            label_visibility="collapsed",
            key="sb_curso"
        )

        st.markdown("<div style='margin-top: 28px;'></div>", unsafe_allow_html=True)
        if st.button("Explorar Grade Curricular", use_container_width=True, type="primary"):
            st.session_state["explorando"] = True
            st.session_state["filtro_unidade_inicial"] = unidade_escolhida if unidade_escolhida != "Todas as unidades..." else "TODAS"
            st.session_state["filtro_curso_inicial"] = curso_escolhido if curso_escolhido != "Todos os cursos..." else "TODOS"
            st.rerun()
    st.stop()

# ==============================================================================
# FILTRAGEM POR UNIDADE E CURSO (SE SELECIONADOS)
# ==============================================================================
unidade_filtro = st.session_state.get("filtro_unidade_inicial", "TODAS")
curso_filtro_inicial = st.session_state.get("filtro_curso_inicial", "TODOS")

# 1. Mapeia curso -> instituto de forma exata e em minúsculas para robustez
curso_to_inst = {}
if not curso_instituto.empty and "curso" in curso_instituto.columns and "instituto" in curso_instituto.columns:
    for _, row in curso_instituto.iterrows():
        c = str(row.get("curso", "")).strip()
        i = str(row.get("instituto", "")).strip()
        if c and i:
            curso_to_inst[c] = i

# 2. Cursos pertencentes à unidade selecionada
cursos_da_unidade = set(curso_to_inst.keys())
if unidade_filtro != "TODAS":
    cursos_da_unidade = {
        c for c, inst in curso_to_inst.items() 
        if unidade_filtro.strip().lower() == inst.strip().lower() or unidade_filtro.strip().lower() in inst.strip().lower()
    }

# 3. Cursos permitidos finais
if curso_filtro_inicial != "TODOS":
    cursos_permitidos = {curso_filtro_inicial}
elif unidade_filtro != "TODAS":
    cursos_permitidos = cursos_da_unidade
else:
    cursos_permitidos = set(curso_to_inst.keys())

lista_cursos_validos = sorted(list(cursos_da_unidade)) if unidade_filtro != "TODAS" else lista_cursos_geral

dados_grafo = {
    "nodes": {},
    "edges": [],
    "problemas": problemas,
    "cursos": lista_cursos_validos,
}

arestas_cursos = {}

for disc, nome in nomes.items():
    registros = ofertas.get(disc, [])
    
    if unidade_filtro == "TODAS" and curso_filtro_inicial == "TODOS":
        registros_filtrados = registros
    else:
        registros_filtrados = [r for r in registros if r["curso"] in cursos_permitidos]

    if not registros_filtrados:
        continue

    cursos_disc = sorted({r["curso"] for r in registros_filtrados if r["curso"]})

    opcoes = {}
    erros = []
    for curso_alvo in cursos_disc:
        info = exp_map.get((disc, curso_alvo))
        if not info:
            continue
        if info["erro"]:
            erros.append({"curso": curso_alvo, "texto": info["texto"], "motivo": info["erro"]})
        for g in info["grupos"]:
            opcoes.setdefault(tuple(g), set()).add(curso_alvo)
            for membro in g:
                if membro in nomes:
                    arestas_cursos.setdefault((membro, disc), set()).add(curso_alvo)

    dados_grafo["nodes"][disc] = {
        "id": disc,
        "nome": nome,
        "ch": int(cargas.get(disc, 0)),
        "ofertas": registros_filtrados,
        "cursos": cursos_disc,
        "opcoes": [
            {"grupo": list(grupo), "cursos": sorted(cs)}
            for grupo, cs in sorted(opcoes.items(), key=lambda kv: (len(kv[0]), kv[0]))
        ],
        "erros": erros,
    }

dados_grafo["edges"] = [
    {"from": orig, "to": dest, "cursos": sorted(cs)}
    for (orig, dest), cs in sorted(arestas_cursos.items())
    if orig in dados_grafo["nodes"] and dest in dados_grafo["nodes"]
]

# ==============================================================================
# INTERFACE COM ABAS DO STREAMLIT
# ==============================================================================
aba_grafo, aba_mapa = st.tabs(["🗺️ Matriz Curricular", "📍 Mapa Georreferenciado"])

with aba_grafo:
    HTML_TEMPLATE = r"""<!DOCTYPE html>
    <html lang="pt-BR">
    <head>
        <meta charset="UTF-8">
        <title>Road to UFBA</title>
        <script src="https://unpkg.com/vis-network/standalone/umd/vis-network.min.js"></script>
        <style>
            * { margin: 0; padding: 0; box-sizing: border-box; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; user-select: none; }
            html, body { width: 100%; height: 100%; overflow: hidden; background-color: #0b0f19; color: #f1f5f9; }
            #network-container { width: 100vw; height: 100vh; position: absolute; top: 0; left: 0; z-index: 1; }

            /* Legenda Superior Direita */
            #legend {
                position: fixed; top: 20px; right: 20px; z-index: 10; display: none;
                background: rgba(15, 23, 42, 0.85); backdrop-filter: blur(14px);
                border: 1px solid rgba(255, 255, 255, 0.1); border-radius: 12px;
                padding: 12px 16px; font-size: 11px; max-height: 85vh; overflow-y: auto;
                box-shadow: 0 8px 32px rgba(0, 0, 0, 0.5);
            }
            .legend-title { font-size: 10px; font-weight: 700; text-transform: uppercase; color: #a78bfa; margin-bottom: 8px; letter-spacing: 0.05em; }
            .legend-item { display: flex; align-items: center; margin-bottom: 7px; cursor: pointer; transition: opacity 0.2s; }
            .legend-item:last-child { margin-bottom: 0; }
            .legend-item:hover { opacity: 0.8; }
            .legend-dot { width: 11px; height: 11px; border-radius: 50%; margin-right: 10px; flex-shrink: 0; }

            /* Barra de Busca */
            #search-container {
                position: fixed; top: 20px; left: 20px; z-index: 12;
                width: 340px; max-width: calc(100vw - 40px); transition: top 0.2s ease;
            }
            body:has(#detail-panel[style*="display: block"]) #search-container { top: auto; bottom: 24px; left: auto; right: 22px; width: 290px; }
            body:has(#detail-panel[style*="display: block"]) #search-results { top: auto; bottom: 52px; }
            #search-box {
                width: 100%; height: 44px; padding: 0 20px 0 42px;
                background: rgba(18, 24, 38, 0.9); backdrop-filter: blur(16px);
                border: 1px solid rgba(255, 255, 255, 0.15); border-radius: 9999px;
                color: #ffffff; font-size: 13px; outline: none;
                box-shadow: 0 10px 25px rgba(0, 0, 0, 0.6); transition: border-color 0.2s, box-shadow 0.2s;
            }
            #search-box:focus { border-color: #a855f7; box-shadow: 0 0 0 3px rgba(168, 85, 247, 0.25); }
            #search-icon { position: absolute; left: 16px; top: 14px; width: 16px; height: 16px; fill: #94a3b8; pointer-events: none; }
            #search-results {
                position: absolute; top: 52px; left: 0; width: 100%; max-height: 240px; overflow-y: auto;
                background: rgba(15, 23, 42, 0.95); backdrop-filter: blur(16px);
                border: 1px solid rgba(255, 255, 255, 0.12); border-radius: 12px;
                display: none; box-shadow: 0 12px 30px rgba(0,0,0,0.6);
            }
            .search-row { padding: 10px 16px; font-size: 12px; cursor: pointer; border-bottom: 1px solid rgba(255,255,255,0.05); }
            .search-row:hover { background: rgba(168, 85, 247, 0.2); color: #c084fc; }

            /* Menu Hamburguer Inferior Esquerdo */
            #menu-btn {
                position: fixed; bottom: 85px; left: 25px; z-index: 20;
                width: 44px; height: 44px; border-radius: 50%;
                background: rgba(18, 24, 38, 0.95); backdrop-filter: blur(16px);
                border: 1px solid rgba(255, 255, 255, 0.18); color: #fff;
                display: flex; flex-direction: column; justify-content: center; align-items: center; gap: 4px;
                cursor: pointer; box-shadow: 0 10px 25px rgba(0, 0, 0, 0.8); transition: transform 0.2s, background 0.2s;
            }
            #menu-btn:hover { transform: scale(1.05); background: rgba(30, 41, 59, 0.95); }
            .menu-bar { width: 18px; height: 2px; background: #cbd5e1; border-radius: 2px; transition: 0.2s; }

            /* Drawer de Filtros */
            #filter-drawer {
                position: fixed; bottom: 140px; left: 25px; z-index: 19;
                width: 320px; max-height: 75vh; overflow-y: auto;
                background: rgba(15, 23, 42, 0.95); backdrop-filter: blur(20px);
                border: 1px solid rgba(255, 255, 255, 0.12); border-radius: 16px;
                padding: 18px; box-shadow: 0 16px 40px rgba(0, 0, 0, 0.7);
                display: none; flex-direction: column; gap: 13px;
            }
            #filter-drawer.open { display: flex; animation: slideUp 0.25s ease-out; }
            @keyframes slideUp { from { opacity: 0; transform: translateY(15px); } to { opacity: 1; transform: translateY(0); } }
            .drawer-title { font-size: 13px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.05em; color: #a78bfa; margin-bottom: 2px; }
            .filter-group label { font-size: 11px; color: #94a3b8; display: block; margin-bottom: 4px; }
            .filter-select {
                width: 100%; height: 32px; background: #1e293b; border: 1px solid rgba(255, 255, 255, 0.12);
                border-radius: 8px; color: #fff; font-size: 12px; padding: 0 8px; outline: none;
            }
            .reset-btn {
                width: 100%; height: 34px; background: transparent; border: 1px solid #a855f7;
                border-radius: 8px; color: #c084fc; font-size: 12px; font-weight: 600; cursor: pointer; transition: 0.2s;
                margin-top: 4px;
            }
            .reset-btn:hover { background: rgba(168, 85, 247, 0.2); }
            .quality-btn { border-color: #38bdf8; color: #38bdf8; }
            .quality-btn:hover { background: rgba(56, 189, 248, 0.15); }
            .quality-btn.tem-problemas { border-color: #f59e0b; color: #f59e0b; }

            /* Painel Lateral de Detalhes */
            #detail-panel {
                position: fixed; top: 20px; left: 20px; z-index: 15;
                width: 340px; max-height: 82vh; overflow-y: auto;
                background: rgba(15, 23, 42, 0.96); backdrop-filter: blur(20px);
                border: 1px solid rgba(255, 255, 255, 0.14); border-radius: 14px;
                padding: 16px; display: none; box-shadow: 0 16px 40px rgba(0, 0, 0, 0.7);
            }
            #detail-panel h3 { font-size: 14px; font-weight: 700; color: #f8fafc; margin-right: 20px; line-height: 1.3; }
            #detail-panel .sub { font-size: 11px; color: #94a3b8; margin: 4px 0 12px 0; }
            #detail-panel .close-btn { position: absolute; top: 12px; right: 14px; cursor: pointer; color: #64748b; font-size: 16px; border: none; background: none; }
            #detail-panel .close-btn:hover { color: #fff; }
            .section-header { font-size: 11px; font-weight: 700; text-transform: uppercase; color: #38bdf8; margin: 12px 0 6px 0; display: flex; justify-content: space-between; align-items: center; }

            /* Cartão de Opção de Pré-requisito */
            .cartao-opcao {
                background: #1e293b; border: 1px solid rgba(255, 255, 255, 0.08); border-radius: 8px;
                padding: 9px 11px; margin-bottom: 7px; cursor: pointer; transition: all 0.2s ease;
            }
            .cartao-opcao:hover { border-color: #facc15; background: rgba(250, 204, 21, 0.06); }
            .cartao-opcao.ativo-opcao {
                border-color: #facc15 !important;
                background: rgba(250, 204, 21, 0.18) !important;
                box-shadow: 0 0 12px rgba(250, 204, 21, 0.3);
            }
            .cartao-title { font-size: 11px; font-weight: 700; color: #94a3b8; margin-bottom: 5px; display: flex; align-items: center; justify-content: space-between; }
            .cartao-opcao.ativo-opcao .cartao-title { color: #facc15; }
            .cartao-cursos { font-size: 10px; font-weight: 400; color: #64748b; margin-left: 6px; }
            .badge-opcao { font-size: 9px; padding: 1px 6px; border-radius: 4px; background: rgba(250, 204, 21, 0.25); color: #facc15; display: none; }
            .cartao-opcao.ativo-opcao .badge-opcao { display: inline-block; }

            .aviso-erro {
                background: rgba(245, 158, 11, 0.12); border: 1px solid rgba(245, 158, 11, 0.45);
                border-radius: 8px; padding: 8px 10px; margin-bottom: 8px; font-size: 11px; color: #fcd34d; line-height: 1.4;
            }
            .aviso-erro code { color: #fde68a; font-family: monospace; user-select: text; }

            .chip {
                display: inline-block; padding: 3px 8px; margin: 2px; border-radius: 6px;
                background: #334155; font-size: 11px; color: #f1f5f9;
            }
            .cartao-opcao.ativo-opcao .chip { background: #475569; color: #fff; border: 1px solid rgba(250, 204, 21, 0.5); }
            .chip.link { cursor: pointer; transition: background 0.15s; }
            .chip.link:hover { background: #38bdf8; color: #0b0f19; font-weight: 600; }
            .chip-plus { color: #94a3b8; font-weight: bold; margin: 0 3px; font-size: 11px; }
            .btn-ver-todos {
                font-size: 10px; color: #38bdf8; background: transparent; border: 1px solid rgba(56, 189, 248, 0.3);
                border-radius: 5px; padding: 2px 7px; cursor: pointer; transition: 0.2s;
            }
            .btn-ver-todos:hover { background: rgba(56, 189, 248, 0.15); }

            /* Modal de Qualidade dos Dados */
            #quality-modal {
                position: fixed; inset: 0; z-index: 40; display: none;
                background: rgba(0, 0, 0, 0.6); align-items: center; justify-content: center;
            }
            #quality-modal.open { display: flex; }
            .quality-box {
                position: relative; width: 640px; max-width: calc(100vw - 40px); max-height: 80vh; overflow-y: auto;
                background: rgba(15, 23, 42, 0.98); border: 1px solid rgba(255, 255, 255, 0.14);
                border-radius: 14px; padding: 18px 20px; box-shadow: 0 16px 40px rgba(0, 0, 0, 0.7);
            }
            .quality-box h3 { font-size: 15px; margin-bottom: 4px; }
            .quality-box .quality-close { position: absolute; top: 12px; right: 14px; cursor: pointer; color: #64748b; font-size: 16px; border: none; background: none; }
            .quality-box .quality-close:hover { color: #fff; }
            .quality-group { margin-top: 14px; }
            .quality-group h4 { font-size: 11px; text-transform: uppercase; letter-spacing: 0.04em; color: #f59e0b; margin-bottom: 6px; }
            .quality-row { font-size: 11px; color: #cbd5e1; padding: 5px 0; border-bottom: 1px solid rgba(255,255,255,0.06); line-height: 1.4; user-select: text; }
            .quality-row b { color: #f8fafc; }
            .quality-row span.curso { color: #94a3b8; }
        </style>
    </head>
    <body>
        <div id="network-container"></div>

        <div id="legend">
            <div class="legend-title" id="legend-header">Semestre Ideal</div>
            <div id="legend-items"></div>
        </div>

        <div id="search-container">
            <svg id="search-icon" viewBox="0 0 24 24"><path d="M10 2a8 8 0 015.293 13.707l5 5a1 1 0 01-1.414 1.414l-5-5A8 8 0 1110 2zm0 2a6 6 0 100 12 6 6 0 000-12z"/></svg>
            <input type="text" id="search-box" placeholder="Buscar disciplina..." autocomplete="off">
            <div id="search-results"></div>
        </div>

        <div id="menu-btn" title="Filtros e Configurações">
            <div class="menu-bar"></div>
            <div class="menu-bar"></div>
            <div class="menu-bar"></div>
        </div>

        <div id="filter-drawer">
            <div class="drawer-title">Configurações & Filtros</div>

            <div class="filter-group">
                <label>Curso:</label>
                <select id="filtro-curso" class="filter-select">
                    <option value="TODOS">Todos os Cursos</option>
                </select>
            </div>

            <div class="filter-group">
                <label>Natureza:</label>
                <select id="filtro-tipo" class="filter-select">
                    <option value="TODOS">Todas (Obrigatórias e Optativas)</option>
                    <option value="OB">Obrigatórias (OB)</option>
                    <option value="OP">Optativas (OP)</option>
                </select>
            </div>

            <div class="filter-group">
                <label>Tamanho dos Nós:</label>
                <select id="filtro-tamanho" class="filter-select">
                    <option value="PADRAO">Tamanho Padrão (Fixo)</option>
                    <option value="CARGA">Por Carga Horária</option>
                    <option value="GRAU">Por Grau de Conexão</option>
                </select>
            </div>

            <div class="filter-group">
                <label>Colorir Nós por:</label>
                <select id="filtro-cor" class="filter-select">
                    <option value="SEMESTRE">Semestre Ideal (Tons de Roxo)</option>
                    <option value="CARGA">Carga Horária</option>
                    <option value="GRAU">Grau de Conexão</option>
                </select>
            </div>

            <div class="filter-group">
                <label>Espessura das Arestas: <span id="val-espessura">1.2</span>px</label>
                <input type="range" id="slider-espessura" min="0.5" max="15.0" step="0.5" value="1.2" style="width: 100%; accent-color: #a855f7; cursor: pointer;">
            </div>

            <button id="btn-reset" class="reset-btn">Resetar Visualização</button>
            <button id="btn-qualidade" class="reset-btn quality-btn">Qualidade dos dados</button>
        </div>

        <div id="detail-panel"></div>

        <div id="quality-modal">
            <div class="quality-box">
                <button class="quality-close" id="quality-close">✕</button>
                <h3 id="quality-title"></h3>
                <div id="quality-body"></div>
            </div>
        </div>

        <script>
        const DADOS_GRAFO = __DADOS_GRAFO__;

        const selectCurso = document.getElementById("filtro-curso");
        DADOS_GRAFO.cursos.forEach(c => {
            const opt = document.createElement("option");
            opt.value = c;
            opt.textContent = c;
            selectCurso.appendChild(opt);
        });

        const cursoInicialPre = __CURSO_INICIAL_PRE__;
        if (cursoInicialPre && cursoInicialPre !== "TODOS") {
            selectCurso.value = cursoInicialPre;
        }

        const COR_PREREQ_GERAL = "#38bdf8";
        const COR_CAMINHO_OPCAO = "#facc15";
        const COR_ARESTA_PADRAO = "rgba(148, 163, 184, 0.28)";
        const COR_ARESTA_APAGADA = "rgba(100, 116, 139, 0.05)";
        const COR_NO_APAGADO = "rgba(30, 41, 59, 0.25)";
        const COR_FONTE_APAGADA = "rgba(100, 116, 139, 0.2)";

        const PALETA_SEMESTRES_ROXO = {
            "1": "#3b0764",
            "2": "#581c87",
            "3": "#6b21a8",
            "4": "#7e22ce",
            "5": "#9333ea",
            "6": "#a855f7",
            "7": "#c084fc",
            "8": "#d8b4fe",
            "9": "#ede9fe",
            "10": "#ffffff",
            "11": "#f3e8ff",
            "12": "#e9d5ff",
            "OPT": "#64748b"
        };

        function esc(texto) {
            return String(texto).replace(/[&<>"']/g, c => (
                { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
            ));
        }

        function atualizarLegenda(modoCor) {
            const header = document.getElementById("legend-header");
            const container = document.getElementById("legend-items");
            container.innerHTML = "";
            document.getElementById("legend").style.display = modoCor === "SEMESTRE" ? "block" : "none";

            if (modoCor === "SEMESTRE") {
                header.textContent = "Semestre Ideal";
                for (let p = 1; p <= 10; p++) {
                    const item = document.createElement("div");
                    item.className = "legend-item";
                    item.innerHTML = `<div class="legend-dot" style="background:${PALETA_SEMESTRES_ROXO[p]}"></div><span>${p}º Semestre Ideal</span>`;
                    container.appendChild(item);
                }
                const itemOpt = document.createElement("div");
                itemOpt.className = "legend-item";
                itemOpt.innerHTML = `<div class="legend-dot" style="background:${PALETA_SEMESTRES_ROXO['OPT']}"></div><span>Optativa / Livre</span>`;
                container.appendChild(itemOpt);
            } else if (modoCor === "CARGA") {
                header.textContent = "Carga Horária";
                const niveis = [
                    { label: "Até 30h", cor: "#818cf8" },
                    { label: "60h (Padrão)", cor: "#a855f7" },
                    { label: "Acima de 60h", cor: "#f43f5e" }
                ];
                niveis.forEach(n => {
                    const item = document.createElement("div");
                    item.className = "legend-item";
                    item.innerHTML = `<div class="legend-dot" style="background:${n.cor}"></div><span>${n.label}</span>`;
                    container.appendChild(item);
                });
            } else if (modoCor === "GRAU") {
                header.textContent = "Conexões (Grau)";
                const niveis = [
                    { label: "Baixa (1-3)", cor: "#64748b" },
                    { label: "Média (4-7)", cor: "#c084fc" },
                    { label: "Alta (8+)", cor: "#ec4899" }
                ];
                niveis.forEach(n => {
                    const item = document.createElement("div");
                    item.className = "legend-item";
                    item.innerHTML = `<div class="legend-dot" style="background:${n.cor}"></div><span>${n.label}</span>`;
                    container.appendChild(item);
                });
            }
        }

        let network = null;
        let visNodes = null;
        let visEdges = null;
        let coresOriginaisNos = {};
        let infoNos = {};
        let noSelecionadoAtual = null;
        let opcaoAtivaAtual = null;

        const cursoAtual = () => document.getElementById("filtro-curso").value;

        function ofertasNoEscopo(n, curso) {
            if (curso === "TODOS") return n.ofertas;
            return n.ofertas.filter(o => o.curso === curso);
        }

        function moda(valores) {
            const cont = {};
            valores.filter(v => v).forEach(v => { cont[v] = (cont[v] || 0) + 1; });
            let melhor = "", max = 0;
            Object.keys(cont)
                .sort((a, b) => a.localeCompare(b, undefined, { numeric: true }))
                .forEach(k => { if (cont[k] > max) { max = cont[k]; melhor = k; } });
            return melhor;
        }

        function resumoNo(n, curso) {
            const regs = ofertasNoEscopo(n, curso);
            return {
                tipo: moda(regs.map(o => o.tipo)),
                periodo: moda(regs.map(o => o.periodo))
            };
        }

        function obterCorNo(node, modoCor) {
            if (modoCor === "CARGA") {
                const ch = node.ch || 0;
                return ch > 60 ? "#f43f5e" : (ch >= 60 ? "#a855f7" : "#818cf8");
            }
            if (modoCor === "GRAU") {
                const g = node.grau || 0;
                return g >= 8 ? "#ec4899" : (g >= 4 ? "#c084fc" : "#64748b");
            }
            return PALETA_SEMESTRES_ROXO[node.periodo] || PALETA_SEMESTRES_ROXO["OPT"];
        }

        function obterTamanhoNo(node, modoTamanho) {
            if (modoTamanho === "CARGA") {
                return Math.max(16, Math.min(65, 16 + ((node.ch || 0) / 2.5)));
            }
            if (modoTamanho === "GRAU") {
                return Math.max(16, Math.min(75, 16 + ((node.grau || 0) * 5.5)));
            }
            return 32;
        }

        function estilizarArestas(fn) {
            visEdges.update(visEdges.get().map(e => Object.assign({ id: e.id }, fn(e))));
        }

        function estilizarNos(fn) {
            visNodes.update(visNodes.get().map(n => Object.assign({ id: n.id }, fn(n))));
        }

        function construirGrafo() {
            const cursoSel = cursoAtual();
            const modoTipo = document.getElementById("filtro-tipo").value;
            const modoTamanho = document.getElementById("filtro-tamanho").value;
            const modoCor = document.getElementById("filtro-cor").value;

            atualizarLegenda(modoCor);

            const nodesArr = [];
            const edgesArr = [];
            const nodesVisiveis = new Set();
            const grau = {};
            coresOriginaisNos = {};
            infoNos = {};

            Object.values(DADOS_GRAFO.nodes).forEach(n => {
                if (cursoSel !== "TODOS" && !n.cursos.includes(cursoSel)) return;
                if (modoTipo !== "TODOS" && !ofertasNoEscopo(n, cursoSel).some(o => o.tipo === modoTipo)) return;
                nodesVisiveis.add(n.id);
                grau[n.id] = 0;
            });

            const espessuraBase = parseFloat(document.getElementById("slider-espessura").value) || 1.2;
            DADOS_GRAFO.edges.forEach((e, idx) => {
                if (!nodesVisiveis.has(e.from) || !nodesVisiveis.has(e.to)) return;
                if (cursoSel !== "TODOS" && !e.cursos.includes(cursoSel)) return;
                grau[e.from]++;
                grau[e.to]++;
                edgesArr.push({
                    id: "e_" + idx,
                    from: e.from,
                    to: e.to,
                    arrows: { to: { enabled: true, scaleFactor: 0.6 } },
                    color: { color: COR_ARESTA_PADRAO, opacity: 0.28 },
                    width: espessuraBase
                });
            });

            nodesVisiveis.forEach(id => {
                const n = DADOS_GRAFO.nodes[id];
                const resumo = resumoNo(n, cursoSel);
                const info = Object.assign({}, n, resumo, { grau: grau[id] });
                infoNos[id] = info;

                const cor = obterCorNo(info, modoCor);
                coresOriginaisNos[id] = { bg: cor, border: cor, font: "#ffffff" };

                const temErro = n.erros.some(er => cursoSel === "TODOS" || er.curso === cursoSel);

                nodesArr.push({
                    id: id,
                    label: id,
                    title: `${id} - ${n.nome}\nCarga Horária: ${n.ch}h\nPeríodo: ${info.periodo ? info.periodo + 'º Semestre' : 'Optativa'}\nGrau: ${info.grau} conexões` + (temErro ? "\n⚠ expressão de pré-requisito não interpretada" : ""),
                    color: {
                        background: cor,
                        border: cor,
                        highlight: { background: "#ffffff", border: "#a855f7" }
                    },
                    font: {
                        color: "#ffffff",
                        size: 12,
                        face: "system-ui, sans-serif",
                        strokeWidth: 2,
                        strokeColor: "#0b0f19",
                        vadjust: 0
                    },
                    size: obterTamanhoNo(info, modoTamanho),
                    shape: "dot",
                    borderWidth: temErro ? 3 : 1,
                    shapeProperties: { borderDashes: temErro ? [4, 3] : false }
                });
            });

            visNodes = new vis.DataSet(nodesArr);
            visEdges = new vis.DataSet(edgesArr);

            const container = document.getElementById("network-container");
            const data = { nodes: visNodes, edges: visEdges };
            const options = {
                physics: {
                    solver: "forceAtlas2Based",
                    forceAtlas2Based: {
                        gravitationalConstant: -90,
                        centralGravity: 0.007,
                        springLength: 110,
                        springConstant: 0.05,
                        damping: 0.35,
                        avoidOverlap: 0.85
                    },
                    stabilization: { iterations: 120 }
                },
                interaction: {
                    hover: true,
                    tooltipDelay: 100,
                    navigationButtons: false,
                    keyboard: false
                }
            };

            if (network) network.destroy();
            document.getElementById("detail-panel").style.display = "none";
            noSelecionadoAtual = null;
            opcaoAtivaAtual = null;
            network = new vis.Network(container, data, options);

            network.on("click", function(params) {
                if (params.nodes.length > 0) {
                    selecionarDisciplina(params.nodes[0]);
                } else {
                    fecharDetalhes();
                }
            });
        }

        function obterTodosPrereqs(id) {
            if (!visEdges || !visNodes) return [];
            const diretos = new Set();
            visEdges.forEach(e => {
                if (e.to === id && visNodes.get(e.from)) diretos.add(e.from);
            });
            return Array.from(diretos);
        }

        function obterLibera(id) {
            const destinos = new Set();
            if (visEdges) visEdges.forEach(e => { if (e.from === id) destinos.add(e.to); });
            return Array.from(destinos).sort();
        }

        function selecionarDisciplina(id) {
            noSelecionadoAtual = id;
            opcaoAtivaAtual = null;
            abrirPainelDetalhes(id);
            destacarTodosPrereqs(id);
        }

        function aplicarDestaque(alvoId, conjuntoNos, arestasAtivas, corAresta, larguraAtiva, escalaSeta, corBordaAlvo, tamanhoFonteAtiva, tamanhoFonteApagada) {
            estilizarArestas(e => {
                const ativa = arestasAtivas.has(e.id);
                return {
                    color: ativa ? { color: corAresta, opacity: 1.0 } : { color: COR_ARESTA_APAGADA, opacity: 0.04 },
                    width: ativa ? larguraAtiva : 1,
                    arrows: { to: { enabled: true, scaleFactor: ativa ? escalaSeta : 0.4 } }
                };
            });

            estilizarNos(n => {
                const orig = coresOriginaisNos[n.id] || { bg: "#64748b", border: "#64748b", font: "#ffffff" };
                if (conjuntoNos.has(n.id)) {
                    return {
                        color: { background: orig.bg, border: corBordaAlvo(n.id, orig) },
                        font: { color: "#ffffff", size: tamanhoFonteAtiva }
                    };
                }
                return {
                    color: { background: COR_NO_APAGADO, border: COR_NO_APAGADO },
                    font: { color: COR_FONTE_APAGADA, size: tamanhoFonteApagada }
                };
            });
        }

        function destacarTodosPrereqs(id) {
            if (!visNodes || !visNodes.get(id)) return;
            document.querySelectorAll('.cartao-opcao').forEach(c => c.classList.remove('ativo-opcao'));
            opcaoAtivaAtual = null;

            const prereqs = obterTodosPrereqs(id);
            const conjuntoNos = new Set([id, ...prereqs]);
            const arestasAtivas = new Set();
            visEdges.forEach(e => { if (e.to === id && conjuntoNos.has(e.from)) arestasAtivas.add(e.id); });

            aplicarDestaque(id, conjuntoNos, arestasAtivas, COR_PREREQ_GERAL, 3.0, 0.85,
                (nid, orig) => nid === id ? "#ffffff" : orig.border, 13, 10);

            if (prereqs.length > 0) {
                network.fit({
                    nodes: [id, ...prereqs],
                    animation: { duration: 600, easingFunction: "easeInOutQuad" }
                });
            } else {
                network.focus(id, {
                    scale: 1.5,
                    animation: { duration: 600, easingFunction: "easeInOutQuad" }
                });
            }
        }

        function destacarCaminhoOpcao(alvoId, prereqsList, cardId) {
            const cartoes = document.querySelectorAll('.cartao-opcao');
            const cartaoClicado = document.getElementById(cardId);
            const jaAtivo = cartaoClicado && cartaoClicado.classList.contains("ativo-opcao");

            cartoes.forEach(c => c.classList.remove("ativo-opcao"));

            if (jaAtivo) {
                destacarTodosPrereqs(alvoId);
                return;
            }

            opcaoAtivaAtual = cardId;
            if (cartaoClicado) cartaoClicado.classList.add("ativo-opcao");

            const conjuntoNos = new Set([alvoId, ...prereqsList]);
            const arestasAtivas = new Set();
            visEdges.forEach(e => { if (e.to === alvoId && prereqsList.includes(e.from)) arestasAtivas.add(e.id); });

            aplicarDestaque(alvoId, conjuntoNos, arestasAtivas, COR_CAMINHO_OPCAO, 4.0, 1.0,
                () => COR_CAMINHO_OPCAO, 14, 9);

            network.fit({
                nodes: [alvoId, ...prereqsList],
                animation: { duration: 550, easingFunction: "easeInOutQuad" }
            });
        }

        function restaurarGrafoCompleto() {
            noSelecionadoAtual = null;
            opcaoAtivaAtual = null;
            if (!visEdges || !visNodes) return;

            const espessuraAtual = parseFloat(document.getElementById("slider-espessura").value) || 1.2;

            estilizarArestas(() => ({
                color: { color: COR_ARESTA_PADRAO, opacity: 0.28 },
                width: espessuraAtual,
                arrows: { to: { enabled: true, scaleFactor: 0.6 } }
            }));

            estilizarNos(n => {
                const orig = coresOriginaisNos[n.id];
                if (!orig) return {};
                return {
                    color: { background: orig.bg, border: orig.border },
                    font: { color: orig.font, size: 12 }
                };
            });
        }

        const panel = document.getElementById("detail-panel");

        function abrirPainelDetalhes(id) {
            const n = DADOS_GRAFO.nodes[id];
            if (!n) return;

            const cursoSel = cursoAtual();
            const resumo = infoNos[id] || resumoNo(n, cursoSel);

            let html = `<button class="close-btn" onclick="fecharDetalhes()">✕</button>`;
            html += `<h3>${esc(n.id)} — ${esc(n.nome)}</h3>`;
            html += `<div class="sub">${n.ch}h | ${resumo.tipo === 'OB' ? 'Obrigatória' : (resumo.tipo === 'OP' ? 'Optativa' : 'Disciplina')} (${resumo.periodo ? resumo.periodo + 'º Sem' : 'Livre'})${cursoSel !== 'TODOS' ? ' — ' + esc(cursoSel) : ''}</div>`;

            const opcoes = n.opcoes.filter(o => cursoSel === "TODOS" || o.cursos.includes(cursoSel));
            const erros = n.erros.filter(er => cursoSel === "TODOS" || er.curso === cursoSel);

            html += `<div class="section-header">
                <span>Pré-requisitos</span>
                ${opcoes.length > 0 ? `<button class="btn-ver-todos" onclick="destacarTodosPrereqs('${id}')">Ver todos</button>` : ''}
            </div>`;

            erros.forEach(er => {
                html += `<div class="aviso-erro">⚠ Expressão não interpretada${cursoSel === 'TODOS' ? ' (' + esc(er.curso) + ')' : ''}: <code>${esc(er.texto)}</code><br>Motivo: ${esc(er.motivo)}</div>`;
            });

            if (opcoes.length === 0 && erros.length === 0) {
                html += `<div style="font-size: 11px; color: #94a3b8; font-style: italic; margin-bottom: 8px;">Sem pré-requisitos cadastrados.</div>`;
            } else {
                opcoes.forEach((opc, i) => {
                    const cardId = `card_opc_${i + 1}`;
                    const grupoJson = JSON.stringify(opc.grupo).replace(/"/g, '&quot;');
                    
                    // Verifica se todas as matérias do grupo existem no grafo visível (ou seja, pertencem à grade do curso selecionado)
                    const materiasFaltantes = opc.grupo.filter(m => !visNodes || !visNodes.get(m));
                    const isInativado = cursoSel !== "TODOS" && materiasFaltantes.length > 0;

                    const rotuloCursos = cursoSel === "TODOS" ? `<span class="cartao-cursos">${opc.cursos.map(esc).join(", ")}</span>` : "";
                    const avisoInativo = isInativado ? `<div style="font-size: 10px; color: #f87171; margin-top: 4px;">⚠ Matéria(s) ausente(s) na grade deste curso: ${materiasFaltantes.map(esc).join(", ")}</div>` : "";

                    html += `
                    <div id="${cardId}" class="cartao-opcao ${isInativado ? 'inativo-opcao' : ''}" ${isInativado ? '' : `onclick="destacarCaminhoOpcao('${id}', ${grupoJson}, '${cardId}')"`} style="${isInativado ? 'opacity: 0.5; border-color: rgba(248, 113, 113, 0.4); cursor: not-allowed;' : ''}">
                        <div class="cartao-title">
                            <span>Opção ${i + 1}${rotuloCursos}</span>
                            <span class="badge-opcao">Ativa</span>
                        </div>
                        <div>
                            ${opc.grupo.map((m, k) => `${k > 0 ? '<span class="chip-plus">+</span>' : ''}<span class="chip" ${!visNodes || !visNodes.get(m) ? 'style="background: #7f1d1d; color: #fca5a5;"' : ''}>${esc(m)}</span>`).join("")}
                        </div>
                        ${avisoInativo}
                    </div>`;
                });
            }

            const libera = obterLibera(id);
            if (libera.length > 0) {
                html += `<div class="section-header" style="color: #a78bfa; margin-top: 14px;">Libera (${libera.length})</div>`;
                html += `<div>`;
                libera.forEach(lib => {
                    html += `<span class="chip link" onclick="selecionarDisciplina('${lib}')">${esc(lib)}</span>`;
                });
                html += `</div>`;
            }

            panel.innerHTML = html;
            panel.style.display = "block";
        }

        window.fecharDetalhes = function() {
            panel.style.display = "none";
            restaurarGrafoCompleto();
        };

        const searchInput = document.getElementById("search-box");
        const searchResults = document.getElementById("search-results");

        searchInput.addEventListener("input", function() {
            const termo = this.value.trim().toLowerCase();
            if (!termo) { searchResults.style.display = "none"; return; }

            const encontrados = Object.values(DADOS_GRAFO.nodes).filter(n =>
                n.id.toLowerCase().includes(termo) || n.nome.toLowerCase().includes(termo)
            ).slice(0, 8);

            if (encontrados.length === 0) {
                searchResults.innerHTML = `<div class="search-row" style="color:#64748b;">Nenhuma matéria encontrada</div>`;
            } else {
                searchResults.innerHTML = encontrados.map(n =>
                    `<div class="search-row" onclick="selecionarBusca('${n.id}')"><b>${esc(n.id)}</b> — ${esc(n.nome)}</div>`
                ).join("");
            }
            searchResults.style.display = "block";
        });

        window.selecionarBusca = function(id) {
            searchInput.value = "";
            searchResults.style.display = "none";
            if (!visNodes || !visNodes.get(id)) {
                document.getElementById("filtro-curso").value = "TODOS";
                document.getElementById("filtro-tipo").value = "TODOS";
                construirGrafo();
            }
            selecionarDisciplina(id);
        };

        const menuBtn = document.getElementById("menu-btn");
        const filterDrawer = document.getElementById("filter-drawer");
        menuBtn.addEventListener("click", () => {
            filterDrawer.classList.toggle("open");
        });

        document.getElementById("filtro-curso").addEventListener("change", construirGrafo);
        document.getElementById("filtro-tipo").addEventListener("change", construirGrafo);
        document.getElementById("filtro-tamanho").addEventListener("change", construirGrafo);
        document.getElementById("filtro-cor").addEventListener("change", construirGrafo);

        const sliderEspessura = document.getElementById("slider-espessura");
        const valEspessura = document.getElementById("val-espessura");
        sliderEspessura.addEventListener("input", function() {
            valEspessura.textContent = this.value;
            const esp = parseFloat(this.value);
            if (visEdges && !noSelecionadoAtual) {
                visEdges.update(visEdges.get().map(e => ({ id: e.id, width: esp })));
            }
        });

        document.getElementById("btn-reset").addEventListener("click", () => {
            document.getElementById("filtro-curso").value = "TODOS";
            document.getElementById("filtro-tipo").value = "TODOS";
            document.getElementById("filtro-tamanho").value = "PADRAO";
            document.getElementById("filtro-cor").value = "SEMESTRE";
            construirGrafo();
            if (network) network.fit({ animation: { duration: 500 } });
        });

        const ROTULOS_PROBLEMA = {
            EXPRESSAO_INVALIDA: "Expressões de pré-requisito que não puderam ser interpretadas",
            REFERENCIA_INEXISTENTE: "Pré-requisitos que não existem em disciplinas.csv",
            EXPRESSAO_SEM_OFERTA: "Expressões para disciplina não ofertada no curso (ignoradas)",
            EXPRESSAO_DUPLICADA: "Mais de uma expressão para a mesma disciplina/curso",
            AUTO_REFERENCIA: "Disciplina que é pré-requisito de si mesma",
            OFERTA_SEM_DISCIPLINA: "Ofertas de disciplinas ausentes em disciplinas.csv"
        };

        const btnQualidade = document.getElementById("btn-qualidade");
        const qtdProblemas = DADOS_GRAFO.problemas.length;
        btnQualidade.textContent = qtdProblemas > 0 ? `Qualidade dos dados (${qtdProblemas} alertas)` : "Qualidade dos dados (OK)";
        if (qtdProblemas > 0) btnQualidade.classList.add("tem-problemas");

        function abrirQualidade() {
            const ps = DADOS_GRAFO.problemas;
            document.getElementById("quality-title").textContent =
                ps.length > 0 ? `${ps.length} problema(s) encontrado(s) nos dados` : "Nenhum problema encontrado nos dados";

            const grupos = {};
            ps.forEach(p => { (grupos[p.tipo] = grupos[p.tipo] || []).push(p); });

            let html = "";
            Object.keys(grupos).forEach(tipo => {
                html += `<div class="quality-group"><h4>${esc(ROTULOS_PROBLEMA[tipo] || tipo)} (${grupos[tipo].length})</h4>`;
                grupos[tipo].forEach(p => {
                    html += `<div class="quality-row"><b>${esc(p.disciplina)}</b> <span class="curso">${p.curso ? '· ' + esc(p.curso) : ''}</span><br>${esc(p.detalhe)}</div>`;
                });
                html += `</div>`;
            });
            document.getElementById("quality-body").innerHTML = html;
            document.getElementById("quality-modal").classList.add("open");
        }

        btnQualidade.addEventListener("click", abrirQualidade);
        document.getElementById("quality-close").addEventListener("click", () => {
            document.getElementById("quality-modal").classList.remove("open");
        });
        document.getElementById("quality-modal").addEventListener("click", (ev) => {
            if (ev.target.id === "quality-modal") ev.currentTarget.classList.remove("open");
        });

        construirGrafo();
        </script>
    </body>
    </html>
    """

    html_app = HTML_TEMPLATE.replace("__DADOS_GRAFO__", json.dumps(dados_grafo, ensure_ascii=False)).replace("__CURSO_INICIAL_PRE__", json.dumps(curso_filtro_inicial, ensure_ascii=False))
    components.html(html_app, height=1000, scrolling=False)

with aba_mapa:
    st.markdown("""
    <style>
        .stExpander {
            background-color: rgba(15, 23, 42, 0.85) !important;
            border: 1px solid rgba(255, 255, 255, 0.12) !important;
            border-radius: 12px !important;
            margin-bottom: 16px !important;
        }
    </style>
    """, unsafe_allow_html=True)

    df_unidades = ler_csv(UNIDADES_GEO_CSV)
    df_expr = ler_csv(EXPRESSOES_CSV)
    
    if df_unidades.empty:
        st.warning("Arquivo `unidades_para_georreferenciar.csv` não encontrado ou vazio.")
    else:
        # Descobre quais disciplinas estão atualmente visíveis no grafo filtrado
        disciplinas_visiveis = set(dados_grafo["nodes"].keys())
        
        # Filtra o dataframe de expressões pelas disciplinas visíveis no grafo atual
        if not df_expr.empty and "disciplina_id" in df_expr.columns and "unidade_responsavel" in df_expr.columns:
            df_expr_filtrado = df_expr[df_expr["disciplina_id"].isin(disciplinas_visiveis)]
            contagem_disc = df_expr_filtrado.groupby("unidade_responsavel")["disciplina_id"].nunique().to_dict()
        else:
            contagem_disc = {}
            
        df_unidades["qtd_disciplinas"] = df_unidades["unidade_responsavel"].map(contagem_disc).fillna(0).astype(int)
        
        # Oculta unidades que possuem 0 disciplinas na visualização atual
        df_unidades_filtrado = df_unidades[df_unidades["qtd_disciplinas"] > 0].copy()
        
        # Converte lat e lon para float caso existam preenchidas
        df_unidades_filtrado["latitude"] = pd.to_numeric(df_unidades_filtrado["latitude"], errors="coerce")
        df_unidades_filtrado["longitude"] = pd.to_numeric(df_unidades_filtrado["longitude"], errors="coerce")
        
        if df_unidades_filtrado.empty:
            st.info("ℹ️ Nenhuma unidade possui disciplinas visíveis com os filtros atuais selecionados na Matriz Curricular.")
        else:
            with st.expander("📋 Ver tabela de unidades e dados georreferenciados", expanded=False):
                st.dataframe(df_unidades_filtrado, use_container_width=True)
            
            # Filtra unidades com coordenadas válidas para mostrar no mapa do Streamlit
            df_com_coords = df_unidades_filtrado.dropna(subset=["latitude", "longitude"])
            
            # Fallback: Se as coordenadas estiverem zeradas ou vazias, gera coordenadas em Salvador para exibição
            if df_com_coords.empty or df_com_coords["latitude"].eq(0).all():
                import numpy as np
                np.random.seed(42)
                df_unidades_filtrado["latitude"] = -13.0000 + np.random.uniform(-0.02, 0.02, len(df_unidades_filtrado))
                df_unidades_filtrado["longitude"] = -38.5100 + np.random.uniform(-0.02, 0.02, len(df_unidades_filtrado))
                df_com_coords = df_unidades_filtrado.dropna(subset=["latitude", "longitude"])
                st.info("ℹ️ Preenchendo coordenadas geográficas de exemplo automaticamente para exibição no mapa interativo.")

            if not df_com_coords.empty:
                st.map(df_com_coords, latitude="latitude", longitude="longitude", size="qtd_disciplinas", use_container_width=True)
            else:
                st.info("Nenhuma coordenada (latitude/longitude) preenchida para as unidades filtradas.")
