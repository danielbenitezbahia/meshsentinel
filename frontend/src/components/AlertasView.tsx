import { useState, useEffect, useCallback, useMemo } from "react";
import {
  fetchSmnAlerts, fetchSmnCortoPlazo, fetchEdesOutages,
  fetchFirmsEvents, fetchAnnouncement, fetchPublicChannel, fetchTropocastForecast,
} from "../api";
import type {
  SmnPartidoGroup, SmnCortoPlazoItem, EdesOutage, EdesScraperHealth, FirmsEvent,
  ActiveAnnouncement, PublicChannelMessage, TropocastLocation,
} from "../types";
import FireMap from "./FireMap";
import PropagationMap from "./PropagationMap";

const PUBLIC_CHANNEL_POLL_MS = 30_000;

function minsAgoLabel(mins: number | null): string {
  if (mins == null) return "";
  if (mins < 1) return "recién";
  if (mins < 60) return `hace ${mins} min`;
  const h = Math.floor(mins / 60);
  return `hace ${h}h`;
}

// Mismo agrupado por partido, color de severidad (🟡 Moderate / 🟠 Severe /
// 🔴 Extreme) e ícono por fenómeno que el resumen diario que se manda por
// LoRa a las 11 y 18hs (bbs_system.py: build_smn_messages_by_partido).
function SmnPartidoSection({ group }: { group: SmnPartidoGroup }) {
  return (
    <div className="smn-partido-group">
      <div className="smn-partido-name">📍 {group.partido}</div>
      {group.alerts.map((a, i) => (
        <div key={i} className="smn-alert-row">
          <span className="smn-alert-badge">{a.severity_color}{a.icon}</span>
          <div className="smn-alert-info">
            <div className="smn-alert-title-row">
              <span className="smn-alert-title">{a.title}</span>
              {a.severity_label && <span className="smn-alert-severity-label">{a.severity_label}</span>}
            </div>
            <span className="smn-alert-window">🕐 {a.window_text}</span>
          </div>
        </div>
      ))}
    </div>
  );
}

function SmnCortoPlazoCard({ item }: { item: SmnCortoPlazoItem }) {
  return (
    <div className="alert-card alert-card-critical">
      <div className="alert-card-top">
        <span className="alert-node-name" title={item.phenomenon}>{item.phenomenon || item.title}</span>
        <span className="role-node-item-role">corto plazo</span>
      </div>
      {item.affected_partidos.length > 0 && (
        <div className="alert-time-range">{item.affected_partidos.join(", ")}</div>
      )}
      {item.description_clean && <div className="alert-label">{item.description_clean}</div>}
    </div>
  );
}

function EdesCard({ outage }: { outage: EdesOutage }) {
  return (
    <div className="alert-card alert-card-battery">
      <div className="alert-card-top">
        <span className="alert-node-name" title={outage.locality}>{outage.locality}</span>
        <span className="alert-time-range">{outage.time_start} <span className="alert-arrow">→</span> {outage.time_end}</span>
      </div>
      <div className="alert-label">{outage.outage_date}</div>
      <div className="alert-label">{outage.streets}</div>
    </div>
  );
}

export default function AlertasView() {
  const [smnPartidos, setSmnPartidos] = useState<SmnPartidoGroup[]>([]);
  const [smnCorto, setSmnCorto] = useState<{ items: SmnCortoPlazoItem[]; updated_at: number | null }>({ items: [], updated_at: null });
  const [outages, setOutages] = useState<EdesOutage[]>([]);
  const [edesScrapedAt, setEdesScrapedAt] = useState<number | null>(null);
  const [edesHealth, setEdesHealth] = useState<EdesScraperHealth | null>(null);
  const [firmsEvents, setFirmsEvents] = useState<FirmsEvent[]>([]);
  const [announcement, setAnnouncement] = useState<ActiveAnnouncement | null>(null);
  const [publicMessages, setPublicMessages] = useState<PublicChannelMessage[]>([]);
  const [tropocastLocations, setTropocastLocations] = useState<TropocastLocation[]>([]);
  const [selectedOffsetHour, setSelectedOffsetHour] = useState<number | null>(null);
  const [loading, setLoading] = useState(false);

  const load = useCallback(() => {
    setLoading(true);
    Promise.all([
      fetchSmnAlerts().then((r) => setSmnPartidos(r.partidos)).catch(() => setSmnPartidos([])),
      fetchSmnCortoPlazo().then((r) => setSmnCorto(r)).catch(() => setSmnCorto({ items: [], updated_at: null })),
      fetchEdesOutages().then((r) => { setOutages(r.outages); setEdesScrapedAt(r.scraped_at); setEdesHealth(r.health); }).catch(() => { setOutages([]); setEdesScrapedAt(null); setEdesHealth(null); }),
      fetchFirmsEvents().then((r) => setFirmsEvents(r.events)).catch(() => setFirmsEvents([])),
      fetchAnnouncement().then((r) => setAnnouncement(r.active)).catch(() => setAnnouncement(null)),
      fetchTropocastForecast().then((r) => setTropocastLocations(r.locations)).catch(() => setTropocastLocations([])),
    ]).finally(() => setLoading(false));
  }, []);

  useEffect(() => { load(); }, [load]);

  // Pasos de tiempo disponibles para el slider: los offset_hour de la
  // ubicación con más frames guardados (todas deberían compartir la misma
  // grilla de 3hs, ya que se piden con los mismos parámetros).
  const timeSteps = useMemo(() => {
    const withFrames = tropocastLocations.filter((l) => l.frames.length > 0);
    if (withFrames.length === 0) return [];
    const richest = withFrames.reduce((a, b) => (a.frames.length > b.frames.length ? a : b));
    return richest.frames.map((f) => ({ offsetHour: f.offset_hour, label: f.local_time || f.valid_at }));
  }, [tropocastLocations]);

  useEffect(() => {
    if (selectedOffsetHour == null && timeSteps.length > 0) {
      setSelectedOffsetHour(timeSteps[0].offsetHour);
    }
  }, [timeSteps, selectedOffsetHour]);

  const selectedIndex = Math.max(0, timeSteps.findIndex((s) => s.offsetHour === selectedOffsetHour));
  const stepTime = (delta: number) => {
    const next = selectedIndex + delta;
    if (next >= 0 && next < timeSteps.length) setSelectedOffsetHour(timeSteps[next].offsetHour);
  };

  const loadPublicChannel = useCallback(() => {
    fetchPublicChannel().then((r) => setPublicMessages(r.messages)).catch(() => {});
  }, []);

  useEffect(() => {
    loadPublicChannel();
    const id = setInterval(loadPublicChannel, PUBLIC_CHANNEL_POLL_MS);
    return () => clearInterval(id);
  }, [loadPublicChannel]);

  const hasSmn = smnPartidos.length > 0 || smnCorto.items.length > 0;

  return (
    <div className="stats-view">
      <div className="stats-header">
        <span className="stats-title">Alertas</span>
        <button className="btn-refresh" onClick={() => { load(); loadPublicChannel(); }} disabled={loading}>
          {loading ? "…" : "↺"}
        </button>
      </div>

      {announcement && (
        <div className="cartelera-banner">
          <span className="cartelera-icon">📌</span>
          <div>
            <div className="cartelera-text">{announcement.text}</div>
            <div className="cartelera-expires">Vigente hasta {announcement.expires}</div>
          </div>
        </div>
      )}

      <div className="stats-row">
        <div className="stats-chart-card">
          <div className="chart-title">Alertas meteorológicas SMN</div>
          {!hasSmn ? (
            <div className="rank-empty">Sin alertas vigentes</div>
          ) : (
            <>
              {smnCorto.items.length > 0 && (
                <div className="alerts-list" style={{ marginBottom: smnPartidos.length > 0 ? 14 : 0 }}>
                  {smnCorto.items.map((item) => <SmnCortoPlazoCard key={item.dc_date} item={item} />)}
                </div>
              )}
              {smnPartidos.map((g) => <SmnPartidoSection key={g.partido} group={g} />)}
            </>
          )}
        </div>

        <div className="stats-chart-card">
          <div className="chart-title">Cortes de luz programados (EDES)</div>
          {edesHealth?.looks_broken && (
            <div className="scraper-health-warning">
              ⚠️ La página de EDES sigue publicando avisos, pero hace varias corridas que no se
              puede extraer ningún corte de adentro. Probablemente cambió el formato de la página
              de nuevo y hay que revisar el scraper.
            </div>
          )}
          {outages.length === 0 ? (
            <div className="rank-empty">Sin cortes programados</div>
          ) : (
            <div className="alerts-list">
              {outages.map((o, i) => <EdesCard key={i} outage={o} />)}
            </div>
          )}
          {edesScrapedAt && (
            <div className="alert-label" style={{ marginTop: 8 }}>
              Último chequeo: {new Date(edesScrapedAt * 1000).toLocaleString("es-AR")}
            </div>
          )}
        </div>
      </div>

      <div className="stats-row">
        <div className="stats-chart-card">
          <div className="chart-title">Focos térmicos (FIRMS)</div>
          <div className="fire-map-wrap">
            <FireMap events={firmsEvents} />
          </div>
        </div>

        <div className="stats-chart-card">
          <div className="chart-title">Propagación troposférica (Tropocast) · próximas 72hs</div>
          {tropocastLocations.length === 0 ? (
            <div className="rank-empty">Sin pronóstico guardado todavía</div>
          ) : (
            <>
              <div className="fire-map-wrap">
                <PropagationMap locations={tropocastLocations} offsetHour={selectedOffsetHour} />
              </div>
              {timeSteps.length > 0 && (
                <div className="tropocast-time-control">
                  <div className="tropocast-time-label">{timeSteps[selectedIndex]?.label}</div>
                  <div className="tropocast-time-row">
                    <button
                      className="hm-nav-btn"
                      onClick={() => stepTime(-1)}
                      disabled={selectedIndex <= 0}
                    >←</button>
                    <input
                      type="range"
                      min={0}
                      max={timeSteps.length - 1}
                      value={selectedIndex}
                      onChange={(e) => setSelectedOffsetHour(timeSteps[Number(e.target.value)].offsetHour)}
                      className="tropocast-time-slider"
                    />
                    <button
                      className="hm-nav-btn"
                      onClick={() => stepTime(1)}
                      disabled={selectedIndex >= timeSteps.length - 1}
                    >→</button>
                  </div>
                </div>
              )}
            </>
          )}
        </div>
      </div>

      <div className="stats-chart-card stats-chart-full">
        <div className="chart-title">Canal público MediumFast · en vivo</div>
        {publicMessages.length === 0 ? (
          <div className="rank-empty">Sin mensajes recientes</div>
        ) : (
          <div className="public-channel-list">
            {publicMessages.map((m, i) => (
              <div key={i} className="public-channel-item">
                <span className="public-channel-sender">{m.short_name || m.long_name || m.node_id}</span>
                <span className="public-channel-text">{m.text}</span>
                <span className="public-channel-time">{minsAgoLabel(m.mins_ago)}</span>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
