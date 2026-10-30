import * as THREE from "three";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { RoomEnvironment } from "three/addons/environments/RoomEnvironment.js";

const $ = (s, el = document) => el.querySelector(s);
const esc = (t) => String(t ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const api = async (url, opts = {}) => {
  const r = await fetch(url, opts);
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.detail || r.statusText);
  return j;
};

let ESTADO = null;
let PEDIDO = null;
let actual = null; // id del pedido abierto
let sondeo = null;

const VISTAS = ["mapa", "previas", "nuevo", "pedido"];
const ESTADOS = {
  en_marcha: ["var(--good)", "En marcha"],
  espera_vos: ["var(--accent)", "Esperando tu OK"],
  listo: ["var(--idle)", "Listo"],
  error: ["var(--bad)", "Con error"],
};

// ------------------------------------------------------------------ navegación
function vista() {
  const h = location.hash.slice(1);
  if (h.startsWith("pedido/")) return ["pedido", h.split("/")[1]];
  return [VISTAS.includes(h) ? h : "mapa", null];
}

async function mostrar() {
  const [v, id] = vista();
  VISTAS.forEach((x) => ($("#v-" + x).hidden = x !== v));
  document.querySelectorAll("nav.tabs a").forEach((a) =>
    a.dataset.v === v ? a.setAttribute("aria-current", "page") : a.removeAttribute("aria-current"));
  if (id) actual = id;
  await refrescar();
}
window.addEventListener("hashchange", mostrar);

async function refrescar() {
  clearTimeout(sondeo);
  let trabajando = true; // si algo falla (red, servidor reiniciando) se vuelve a intentar
  try {
    ESTADO = await api("/api/estado");
    pintarCabecera();
    const [v] = vista();
    if (v === "mapa") pintarMapa();
    if (v === "previas") pintarPrevias();
    if (v === "nuevo" && !$("#v-nuevo").dataset.ok) pintarNuevo();
    if (v === "pedido" && actual) {
      PEDIDO = await api("/api/pedidos/" + actual);
      pintarPedido();
    }
    trabajando = ESTADO.pedidos.some((p) => p.estado === "en_marcha");
  } finally {
    if (trabajando) sondeo = setTimeout(refrescar, 1500);
  }
}

// ------------------------------------------------------------------ cabecera
function pintarCabecera() {
  const c = (e) => ESTADO.pedidos.filter((p) => p.estado === e).length;
  $("#chips").innerHTML = `
    <span class="chip"><span class="dot" style="background:var(--good)"></span><b>${c("en_marcha")}</b> en marcha</span>
    <span class="chip"><span class="dot" style="background:var(--accent)"></span><b>${c("espera_vos")}</b> esperan tu OK</span>
    <span class="chip"><span class="dot"></span><b>${c("listo")}</b> entregados</span>
    <span class="chip"><span class="dot" style="background:${ESTADO.ia ? "var(--good)" : "var(--warn)"}"></span>${ESTADO.ia ? "IA conectada" : "Sin IA: solo plantillas"}</span>`;
  $("#nprev").textContent = ESTADO.previas.length || "";
  const tab = $("#tabPedido");
  if (actual) {
    const p = ESTADO.pedidos.find((x) => x.id === actual);
    tab.hidden = !p;
    tab.href = "#pedido/" + actual;
    tab.textContent = "Pedido: " + (p ? p.nombre : "");
  }
}

// ---------------------------------------------------------------------- mapa
function pintarMapa() {
  const obj = ESTADO.pedidos.filter((p) => p.linea === "objetos");
  const activo = obj.find((p) => p.estado === "en_marcha") || obj.find((p) => p.estado === "espera_vos");
  const paso = activo ? ESTADO.pasos.findIndex((s) => s.id === activo.paso) : -1;
  const [col, txt] = activo ? ESTADOS[activo.estado] : ["var(--idle)", "Libre"];
  const banner = ESTADO.previas.length ? `
    <div class="banner"><div><div class="t">Tenés ${ESTADO.previas.length} ${ESTADO.previas.length === 1 ? "cosa" : "cosas"} para revisar</div>
    <div class="muted">${ESTADO.previas.map((p) => esc(p.nombre) + " · " + esc(nombrePaso(p.paso))).join(" &nbsp;·&nbsp; ")}</div></div>
    <a class="btn" href="#previas">Ver Previas</a></div>` : "";
  $("#v-mapa").innerHTML = `${banner}
    <div class="legend"><span>Cómo leer el mapa:</span><span>color = una fábrica</span><span>humo = trabajando</span><span>atenuado = por montar</span></div>
    <div class="cols"><span>1 · Lo que entra</span><span>2 · Fábrica</span><span>3 · Revisión</span><span>4 · Destino</span></div>
    <div class="row">
      <div class="lbl">Objetos y muebles</div>
      <div class="flow">
        <div class="node"><b>Fotos, bocetos o una plantilla</b><small>más medidas si las tenés</small></div>
        <a class="fab" href="${activo ? "#pedido/" + activo.id : "#nuevo"}">
          <div class="box" style="background:var(--f-obj)">${activo && activo.estado === "en_marcha" ? '<span class="smoke"></span><span class="smoke"></span>' : ""}
            <svg viewBox="0 0 24 24" fill="none" stroke="#fff" stroke-width="1.8" stroke-linejoin="round"><path d="M12 3l8 4.5v9L12 21l-8-4.5v-9z"/><path d="M12 12l8-4.5M12 12v9M12 12L4 7.5"/></svg></div>
          <div style="min-width:0"><h3>Objetos y muebles</h3><small class="muted small">diseña la ficha, modela por perfiles, revisa y fotografía</small>
            <div class="status"><span class="dot" style="background:${col}"></span>${txt}${activo ? " · " + esc(activo.nombre) + ` en paso ${paso + 1} de ${ESTADO.pasos.length}` : ""}</div></div>
        </a>
        <div class="node"><b>Vos aprobás</b><small>2 veces: boceto y final, en Previas</small></div>
        <div class="dest">SketchUp / Blender<small>DAE por piezas en mm, OBJ, STL, GLB y despiece</small></div>
      </div>
    </div>
    <div class="row off">
      <div class="lbl">Arquitectura y planos</div>
      <div class="flow">
        <div class="node"><b>Plano o planta</b><small>foto o PDF</small></div>
        <div class="fab"><div class="box" style="background:var(--f-arq)"><svg viewBox="0 0 24 24" fill="none" stroke="#fff" stroke-width="1.8"><path d="M4 4h16v16H4zM4 12h7M11 4v16M15 12h5"/></svg></div>
          <div><h3>Arquitectura</h3><small class="muted small">muros extruidos con vanos de puertas y ventanas</small>
          <div class="status"><span class="dot"></span>Por montar · etapa 2</div></div></div>
        <div class="node"><b>Vos en Previas</b><small>antes de exportar</small></div>
        <div class="dest">SketchUp<small>muros como grupos editables</small></div>
      </div>
    </div>
    <h2 style="margin-top:8px">Pedidos</h2>
    <div class="list">${obj.length ? obj.map(itemPedido).join("") : '<p class="muted">Todavía no hay pedidos. <a href="#nuevo">Hacé el primero</a>.</p>'}</div>`;
}

const nombrePaso = (id) => (ESTADO.pasos.find((s) => s.id === id) || {}).nombre || id;

function itemPedido(p) {
  const [col, txt] = ESTADOS[p.estado] || ESTADOS.en_marcha;
  const img = p.miniatura ? `<img src="/pedidos/${p.id}/${p.miniatura}" alt="">` : '<div class="ph"></div>';
  const extra = [p.caras ? p.caras.toLocaleString("es-AR") + " caras" : "", p.nota != null ? "revisor " + String(p.nota).replace(".", ",") + "/5" : ""].filter(Boolean).join(" · ");
  return `<a class="item" href="#pedido/${p.id}">${img}<div style="min-width:0"><b>${esc(p.nombre)}</b>
    <div class="status"><span class="dot" style="background:${col}"></span>${txt} · ${esc(nombrePaso(p.paso))}</div>
    <div class="muted small">${esc(p.creado)}${extra ? " · " + extra : ""}</div></div>
    <span class="btn ${p.estado === "espera_vos" ? "accent" : "ghost"}">${p.estado === "espera_vos" ? "Revisar" : "Ver"}</span></a>`;
}

// -------------------------------------------------------------------- previas
function pintarPrevias() {
  const ap = ESTADO.aprendizajes;
  $("#v-previas").innerHTML = `
    <div><h2>Previas</h2><p class="muted" style="margin:4px 0 0">Todo lo que espera tu OK. Los cambios que pidas quedan guardados y la fábrica los usa en el próximo pedido.</p></div>
    <div class="list">${ESTADO.previas.length ? ESTADO.previas.map(itemPedido).join("") : '<p class="muted">Nada pendiente.</p>'}</div>
    <div class="panel"><h3>Lo que la fábrica aprendió de vos</h3>
      ${ap.length ? `<ul class="muted" style="margin:8px 0 0;padding-left:18px;display:grid;gap:4px">${ap.map((a) => `<li>${esc(a.texto)} <span class="small">(${esc(a.pedido)}, ${esc(a.fecha)})</span></li>`).join("")}</ul>`
        : '<p class="muted small" style="margin:6px 0 0">Todavía nada. Cuando pidas un cambio con "Recordar para los próximos pedidos", aparece acá.</p>'}
    </div>`;
}

// ---------------------------------------------------------------------- nuevo
function pintarNuevo() {
  const el = $("#v-nuevo");
  el.dataset.ok = "1";
  el.innerHTML = `
    <div><h2>Nuevo pedido</h2><p class="muted" style="margin:4px 0 0">Subí fotos desde varios ángulos, un boceto o un plano. Cuantas más vistas, más fiel sale.</p></div>
    <form class="form panel" id="fNuevo">
      <label>Nombre del pedido<input type="text" name="nombre" required placeholder="Manija Schlage F40 Jazz"></label>
      <div class="drop" id="drop"><b>Arrastrá las fotos acá</b><div class="muted small">o <label style="display:inline;color:var(--accent);cursor:pointer;text-decoration:underline">elegilas<input type="file" name="fotos" id="inFotos" accept="image/*" multiple hidden></label> · JPG o PNG, hasta 6</div>
        <div class="fotos" id="prevFotos" style="justify-content:center;margin-top:10px"></div></div>
      <div class="two">
        <label>Medidas que conocés <span class="hint">las tuyas mandan sobre lo que estime la IA</span>
          <input type="text" name="medidas" placeholder="roseta 66 mm, largo palanca 117 mm, puerta 40 mm"></label>
        <label>Presupuesto de caras <span class="hint">vacío = el de la categoría (~4.500 en herrajes)</span>
          <input type="number" name="presupuesto" min="200" step="100" placeholder="4500"></label>
      </div>
      <label>Notas para el diseñador<textarea name="notas" rows="3" placeholder="Qué es, qué piezas querés separadas, qué no hace falta modelar…"></textarea></label>
      <label>Plantilla <span class="hint">para probar la línea sin IA, o partir de una pieza ya aprobada</span>
        <select name="plantilla"><option value="">Ninguna: diseñar desde las fotos ${ESTADO.ia ? "" : "(requiere IA)"}</option>
        ${ESTADO.plantillas.map((p) => `<option value="${p.id}">${esc(p.nombre)}</option>`).join("")}</select></label>
      <div class="acts"><button class="btn accent" type="submit">Mandar a la fábrica</button><span class="muted small" id="msgNuevo"></span></div>
    </form>`;
  const archivos = [];
  const prev = () => ($("#prevFotos").innerHTML = archivos.map((f) => `<img src="${URL.createObjectURL(f)}" alt="">`).join(""));
  const sumar = (lista) => { for (const f of lista) if (f.type.startsWith("image/") && archivos.length < 6) archivos.push(f); prev(); };
  $("#inFotos").addEventListener("change", (e) => sumar(e.target.files));
  const d = $("#drop");
  d.addEventListener("dragover", (e) => { e.preventDefault(); d.classList.add("on"); });
  d.addEventListener("dragleave", () => d.classList.remove("on"));
  d.addEventListener("drop", (e) => { e.preventDefault(); d.classList.remove("on"); sumar(e.dataTransfer.files); });
  $("#fNuevo").addEventListener("submit", async (e) => {
    e.preventDefault();
    const fd = new FormData(e.target);
    fd.delete("fotos");
    archivos.forEach((f) => fd.append("fotos", f));
    if (!fd.get("presupuesto")) fd.delete("presupuesto");
    $("#msgNuevo").textContent = "Enviando…";
    try {
      const p = await api("/api/pedidos", { method: "POST", body: fd });
      delete el.dataset.ok;
      location.hash = "#pedido/" + p.id;
    } catch (err) { $("#msgNuevo").textContent = err.message; }
  });
}

// --------------------------------------------------------------------- pedido
const url = (r) => `/pedidos/${PEDIDO.id}/${r}`;
const galeria = (lista, big) => `<div class="gal ${big ? "big" : ""}">${(lista || []).map((r) => `<a href="${url(r)}" target="_blank"><img src="${url(r)}" alt="" loading="lazy"></a>`).join("")}</div>`;

function pintarPedido() {
  const p = PEDIDO;
  const idx = ESTADO.pasos.findIndex((s) => s.id === p.paso);
  const fin = p.estado === "listo";
  const pasos = ESTADO.pasos.map((s, i) => {
    const st = fin || i < idx ? "done" : i === idx ? "now" : "";
    return `<li class="${st}${s.humano ? " human" : ""}"><span class="ic">${st === "done" ? "✓" : i + 1}</span><div><b style="font-weight:${i === idx ? 600 : 500}">${esc(s.nombre)}</b><small>${esc(s.detalle)}</small></div></li>`;
  }).join("");
  const rev = p.revision;
  const u = p.uso_ia;
  $("#v-pedido").innerHTML = `
    <aside class="panel">
      <div class="tag">Objetos y muebles · pedido ${esc(p.id)}</div>
      <h2 style="margin-top:4px">${esc(p.nombre)}</h2>
      <div class="muted small">${esc(p.creado)}${p.datos.plantilla ? " · plantilla" : ""}</div>
      <ol class="steps">${pasos}</ol>
      <dl class="ficha">
        <dt>Caras</dt><dd>${rev ? rev.total_caras.toLocaleString("es-AR") : "—"} ${p.ficha ? `<span class="muted">de ${p.ficha.presupuesto_caras.toLocaleString("es-AR")}</span>` : ""}</dd>
        <dt>Piezas</dt><dd>${p.ficha ? p.ficha.piezas.length : "—"}</dd>
        <dt>Revisor</dt><dd>${rev ? String(rev.nota).replace(".", ",") + " de 5" : "—"}</dd>
        <dt>Ficha</dt><dd>${p.ficha_version ? "versión " + p.ficha_version : "—"}</dd>
        <dt>Costo IA</dt><dd>${u.costo_usd ? "US$ " + u.costo_usd.toFixed(2).replace(".", ",") : "US$ 0"} <span class="muted small">${(u.entrada + u.salida).toLocaleString("es-AR")} tokens</span></dd>
      </dl>
      ${p.fotos.length ? `<div style="margin-top:14px"><div class="lbl" style="margin-bottom:6px">Fotos de referencia</div><div class="fotos">${p.fotos.map((f) => `<a href="${url("fotos/" + f)}" target="_blank"><img src="${url("fotos/" + f)}" alt=""></a>`).join("")}</div></div>` : ""}
    </aside>
    <div class="panel" id="work">${trabajo(p)}${avanzado(p)}</div>`;
  conectar(p);
}

function trabajo(p) {
  if (p.estado === "error") return `<div class="blk"><div class="err"><b class="st-falla">Se frenó en ${esc(nombrePaso(p.paso))}</b><div>${esc(p.error)}</div>
    <div class="acts"><button class="btn" id="bReintentar">Reintentar</button></div></div></div>`;
  if (p.estado === "en_marcha") return `<div class="blk"><div class="working"><div class="box"><span class="smoke"></span><span class="smoke"></span></div>
    <div><div class="tag">Trabajando</div><h2>${esc(nombrePaso(p.paso))}</h2><div class="muted small">${esc((p.historial.at(-1) || {}).texto)}</div></div></div>
    ${p.renders.boceto ? galeria(p.renders.boceto) : ""}</div>`;
  if (p.paso === "ok_boceto") return `
    <div class="blk"><div class="tag">Paso 4 · tu decisión</div><h2>Aprobá el boceto</h2>
      <p class="muted" style="margin:0">Modelo gris y liviano para juzgar forma y proporciones. Todavía sin cotas ni material.</p>
      ${galeria(p.renders.boceto, true)}
      ${decision("Aprobar boceto")}</div>
    <div class="blk"><h3>Despiece que propone el diseñador</h3>${tablaPiezas(p)}</div>`;
  if (p.paso === "ok_final") return `
    <div class="blk"><div class="tag">Paso 8 · tu decisión</div><h2>Aprobá el modelo final</h2>
      ${visor()}
      ${galeria(p.renders.final)}
      ${decision("Aprobar y entregar")}</div>
    ${bloqueRevisor(p)}`;
  if (p.estado === "listo") {
    const a = p.archivos;
    const link = (k, t) => `<a class="btn ${k === "dae" ? "accent" : "ghost"}" href="${url(a[k])}" download>${t}</a>`;
    return `
    <div class="blk"><div class="tag">Entregado</div><h2>${esc(p.nombre)} está listo</h2>
      <div class="done-card"><b class="st-ok">✓ Archivos listos</b>
        <div class="small">El DAE trae cada pieza como un grupo con nombre, en milímetros y con Z arriba: en SketchUp, Archivo → Importar.</div>
        <div class="acts">${link("dae", "SketchUp (.dae)")}${link("dae_despiece", "DAE despiece")}${link("obj", "OBJ")}${link("stl", "STL")}${link("glb", "GLB")}${link("csv", "Despiece .csv")}</div></div>
      ${visor()}
      ${galeria(p.renders.final)}</div>
    <div class="blk"><h3>Ficha técnica</h3>${tablaFicha(p)}</div>
    ${bloqueRevisor(p)}`;
  }
  return "";
}

const decision = (ok) => `
  <div class="acts"><button class="btn accent" id="bAprobar">${ok}</button><button class="btn ghost" id="bCambio">Pedir un cambio</button></div>
  <div id="cajaCambio" hidden class="blk" style="margin:0;padding:0;border:0">
    <textarea id="txtCambio" rows="3" placeholder="Ej.: la palanca más fina en la punta, la roseta más plana…"></textarea>
    <label class="check"><input type="checkbox" id="chkRecordar" checked> Recordar para los próximos pedidos</label>
    <div class="acts"><button class="btn" id="bMandarCambio">Mandar al diseñador</button><span class="muted small" id="msgCambio"></span></div>
  </div>`;

function tablaPiezas(p) {
  if (!p.ficha) return "";
  return `<div class="overflow"><table><thead><tr><th>Pieza</th><th>Tipo</th><th>Evidencia</th></tr></thead><tbody>
    ${p.ficha.piezas.map((x) => `<tr><td><b>${esc(x.nombre)}</b><div class="muted mono">${esc(x.id)}</div></td><td>${esc(x.tipo || "copia de " + x.copia_de)}</td><td class="muted small">${esc(x.evidencia || "(la misma que " + x.copia_de + ")")}</td></tr>`).join("")}
  </tbody></table></div>`;
}

function tablaFicha(p) {
  const f = p.ficha_tecnica || [];
  return `<div class="overflow"><table><thead><tr><th>Pieza</th><th>Medidas (mm)</th><th>Material</th><th>Caras</th><th></th></tr></thead><tbody>
    ${f.map((x) => {
      const pct = x.caras_max ? Math.min(100, (x.caras / x.caras_max) * 100) : 0;
      return `<tr><td><b>${esc(x.pieza)}</b><div class="muted small">${esc(x.lado)}</div></td><td class="mono">${esc(x.medidas_mm)}</td><td>${esc(x.material.replace("_", " "))}</td>
        <td class="n">${x.caras}${x.caras_max ? ` <span class="muted">/ ${x.caras_max}</span>` : ""}</td><td style="width:18%"><div class="bar"><i style="width:${pct}%"></i></div></td></tr>`;
    }).join("")}</tbody></table></div>`;
}

function bloqueRevisor(p) {
  const r = p.revision;
  if (!r) return "";
  const vueltas = p.vueltas.map((v) => `Vuelta ${v.n}: ${v.fallas.length ? esc(v.fallas.join("; ")) : "todo en orden"}${v.cambios.length ? " → " + esc(v.cambios.join("; ")) : ""}.`).join("<br>");
  return `<div class="blk"><h3>Revisor · ${String(r.nota).replace(".", ",")} de 5 · ${r.aprobado ? `pasó en la vuelta ${p.vueltas.length || 1}` : "con observaciones"}</h3>
    <div class="overflow"><table><thead><tr><th>Criterio</th><th>Estado</th></tr></thead><tbody>
    ${r.criterios.map((c) => `<tr><td>${esc(c.criterio)}<div class="muted small">${esc(c.detalle)}</div></td><td class="st-${c.estado}">${{ ok: "✓ ok", falla: "✗ falla", aviso: "! aviso" }[c.estado]}</td></tr>`).join("")}
    </tbody></table></div>
    ${vueltas ? `<p class="muted small" style="margin:0">${vueltas}</p>` : ""}</div>`;
}

function avanzado(p) {
  if (!p.ficha) return "";
  return `<div class="blk"><details><summary>Ficha de diseño (avanzado)</summary>
    <p class="muted small">Es lo que la IA escribió y lo que arma el motor: perfiles, caminos y medidas en mm. Si la editás, se rehace el boceto.</p>
    <textarea class="code" id="txtFicha">${esc(JSON.stringify(p.ficha, null, 1))}</textarea>
    <div class="acts" style="margin-top:8px"><button class="btn" id="bFicha">Guardar y rehacer boceto</button><span class="muted small" id="msgFicha"></span></div>
  </details>
  <details><summary>Historial</summary><ul class="hist" style="margin-top:8px">${p.historial.map((h) => `<li><span class="muted mono">${esc(h.t)}</span><span>${esc(h.texto)}</span></li>`).join("")}</ul></details></div>`;
}

function conectar(p) {
  const on = (id, fn) => { const b = $("#" + id); if (b) b.addEventListener("click", fn); };
  on("bReintentar", async () => { await api(`/api/pedidos/${p.id}/reintentar`, { method: "POST" }); setTimeout(refrescar, 300); });
  on("bAprobar", async (e) => { e.target.disabled = true; await api(`/api/pedidos/${p.id}/aprobar`, { method: "POST" }); setTimeout(refrescar, 300); });
  on("bCambio", () => { $("#cajaCambio").hidden = false; $("#txtCambio").focus(); });
  on("bMandarCambio", async (e) => {
    const texto = $("#txtCambio").value.trim();
    if (!texto) return;
    e.target.disabled = true;
    $("#msgCambio").textContent = "El diseñador está corrigiendo la ficha…";
    try {
      await api(`/api/pedidos/${p.id}/cambio`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ texto, recordar: $("#chkRecordar").checked }) });
      refrescar();
    } catch (err) { $("#msgCambio").textContent = err.message; e.target.disabled = false; }
  });
  on("bFicha", async () => {
    let ficha;
    try { ficha = JSON.parse($("#txtFicha").value); } catch { $("#msgFicha").textContent = "El JSON no es válido"; return; }
    try {
      await api(`/api/pedidos/${p.id}/ficha`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(ficha) });
      refrescar();
    } catch (err) { $("#msgFicha").textContent = err.message; }
  });
  const glb = p.estado === "listo" ? { armado: p.archivos.glb, despiece: p.archivos.glb_despiece } : p.renders.glb ? { armado: p.renders.glb } : null;
  if (glb && $("#visor")) montarVisor($("#visor"), glb);
}

// ---------------------------------------------------------------- visor 3D
const visor = () => `<div class="viewer" id="visor"><div class="vtools"></div></div>`;

function montarVisor(el, glbs) {
  const W = el.clientWidth, H = el.clientHeight;
  const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
  renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
  renderer.setSize(W, H);
  el.prepend(renderer.domElement);
  const scene = new THREE.Scene();
  // Reflejos de estudio para que el metal se vea metal.
  scene.environment = new THREE.PMREMGenerator(renderer).fromScene(new RoomEnvironment(), 0.04).texture;
  const cam = new THREE.PerspectiveCamera(30, W / H, 0.001, 10);
  scene.add(new THREE.HemisphereLight(0xffffff, 0x8a8f99, 0.8));
  const sol = new THREE.DirectionalLight(0xffffff, 2.2); sol.position.set(-1, 2, 1.5); scene.add(sol);
  const rel = new THREE.DirectionalLight(0xffffff, 0.8); rel.position.set(1.5, 0.5, -1); scene.add(rel);
  const ctl = new OrbitControls(cam, renderer.domElement);
  ctl.enableDamping = true;
  const loader = new GLTFLoader();
  let objeto = null;
  const metal = new THREE.MeshStandardMaterial({ color: 0xc4c7cc, metalness: 0.55, roughness: 0.38 });
  const cargar = (ruta, encuadrar) => loader.load(url(ruta), (g) => {
    if (objeto) scene.remove(objeto);
    objeto = g.scene;
    objeto.traverse((o) => {
      if (!o.isMesh) return;
      o.geometry.deleteAttribute("color");
      if (!o.geometry.attributes.normal) o.geometry.computeVertexNormals();
      o.material = metal;
    });
    scene.add(objeto);
    if (encuadrar) {
      const box = new THREE.Box3().setFromObject(objeto);
      const c = box.getCenter(new THREE.Vector3()), r = box.getSize(new THREE.Vector3()).length();
      cam.position.copy(c).add(new THREE.Vector3(-0.55, 0.45, 0.85).multiplyScalar(r * 1.15));
      ctl.target.copy(c); ctl.update();
    }
  });
  cargar(glbs.armado, true);
  if (glbs.despiece) {
    const t = $(".vtools", el);
    t.innerHTML = `<button data-k="armado">Armado</button><button data-k="despiece">Despiece</button>`;
    t.addEventListener("click", (e) => { const k = e.target.dataset.k; if (k) cargar(glbs[k], false); });
  }
  (function loop() { if (!el.isConnected) return renderer.dispose(); ctl.update(); renderer.render(scene, cam); requestAnimationFrame(loop); })();
}

mostrar();
