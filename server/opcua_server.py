import asyncio
import json
import os
import signal
import sys
import traceback
import urllib.request as request
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from asyncua import Client, Server, ua

VALUE_TAGS = [
    "24R",
    "D_TN",
    "D_TS",
    "D_TX",
    "ELEV",
    "H_10D",
    "H_F10",
    "H_FX",
    "H_UVI",
    "H_XD",
    "HUMD",
    "PRES",
    "TEMP",
    "WDIR",
    "WDSD",
    "CITY",
    "D_TNT",
    "D_TXT",
    "H_F10T",
    "H_FXT",
    "VIS",
    "Weather",
]

DEFAULT_CONFIG: dict = {
    "openData": {
        "address": "https://opendata.cwa.gov.tw/api/v1/rest/datastore/",
        "api": "O-A0003-001",
        "auth_key": "CWB-448F9C5A-3C92-44BF-8FD0-D57CE12F7FA5",
        "target": ["466900", "466920", "467050", "467571", "467441"],
        "stations": [
            {"id": "466900", "name": ""},
            {"id": "466920", "name": ""},
            {"id": "467050", "name": ""},
            {"id": "467571", "name": ""},
            {"id": "467441", "name": ""},
        ],
    },
    "opcUA": {
        "url": "opc.tcp://0.0.0.0:48484",
        "mirror_endpoints": [
            {"url": "opc.tcp://lioil.ddnsfree.com:48484", "name": "lioil", "enabled": True},
            {"url": "opc.tcp://ptw.ddnsfree.com:48484", "name": "PTW", "enabled": True},
            {"url": "opc.tcp://LiBoDe.ddnsfree.com:48484", "name": "LiBoDe", "enabled": True},
        ],
        "mirror_station_map": {
            "466900": "W466900",
            "466920": "W466920",
            "467050": "W467050",
            "467441": "W467441",
            "467571": "W467571",
        },
    },
}

# 預設鏡像站台對應（可由 data/config.json 的 mirror_station_map 覆寫）
DEFAULT_MIRROR_STATION_MAP = {
    "466900": "W466900",
    "466920": "W466920",
    "467050": "W467050",
    "467571": "W467571",
    "467441": "W467441",
}
MIRROR_TAG_NAME_MAP = {"24R": "R24"}


def _default_config() -> dict:
    return deepcopy(DEFAULT_CONFIG)


def _format_datetime_str(s: str) -> str:
    """Format ISO-like datetime strings to 'yyyy-MM-dd HH:mm:ss'."""
    if not s or not isinstance(s, str):
        return s
    s = s.strip()
    if not ("-" in s and ":" in s):
        return s
    try:
        dt = datetime.fromisoformat(s)
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        try:
            if s.endswith("Z"):
                s2 = s[:-1]
            else:
                s2 = s
            for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
                try:
                    dt = datetime.strptime(s2, fmt)
                    return dt.strftime("%Y-%m-%d %H:%M:%S")
                except Exception:
                    continue
        except Exception:
            pass
    return s


def _save_config(config_path: str, cfg: dict) -> None:
    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=4)


def _parse_url(url: str) -> tuple[str, int]:
    parsed = urlparse(str(url or "").strip() or "opc.tcp://127.0.0.1:48480")
    host = parsed.hostname or "127.0.0.1"
    port = int(parsed.port or 48480)
    return host, port


def _load_config(config_path: str) -> dict:
    if not os.path.exists(config_path):
        cfg = _default_config()
        _save_config(config_path, cfg)
        return cfg
    with open(config_path, "r", encoding="utf-8") as f:
        return json.load(f)


class _RemoteMirrorWriter:
    def __init__(self, endpoint: str, name: str = "", station_map: dict | None = None) -> None:
        self.endpoint = endpoint
        self.name = name or endpoint
        self._station_map: dict = station_map or DEFAULT_MIRROR_STATION_MAP
        self._client: Client | None = None
        self._nodes: dict[str, dict[str, object]] = {}
        self._last_error: str = ""

    def _report_error(self, message: str) -> None:
        msg = str(message).strip()
        if msg and msg != self._last_error:
            self._last_error = msg
            # intentionally silent: do not emit prints or logs in packaged exe
            return

    async def _disconnect(self) -> None:
        c = self._client
        self._client = None
        self._nodes = {}
        if c is not None:
            try:
                await c.disconnect()
            except Exception:
                pass

    async def _connect_if_needed(self) -> bool:
        if self._client is not None:
            try:
                await self._client.check_connection()
                return True
            except Exception as e:
                self._report_error(f"connection check failed: {e}")
                await self._disconnect()
        try:
            self._client = Client(url=self.endpoint)
            await self._client.connect()
            await self._resolve_nodes()
            self._last_error = ""
            return True
        except Exception as e:
            self._report_error(f"connect failed: {e}")
            await self._disconnect()
            return False

    async def _resolve_nodes(self) -> None:
        if self._client is None:
            return
        resolved: dict[str, dict[str, object]] = {}

        for sid, remote_sid in self._station_map.items():
            row_nodes: dict[str, object] = {}
            for tag in VALUE_TAGS:
                remote_tag = MIRROR_TAG_NAME_MAP.get(tag, tag)
                nodeid = f"ns=4;s=Root.OpenData.{remote_sid}.{remote_tag}"
                row_nodes[tag] = self._client.get_node(nodeid)
            resolved[sid] = row_nodes

        self._nodes = resolved

    async def _coerce_value(self, node, text: str):
        try:
            vt = await node.read_data_type_as_variant_type()
        except Exception:
            return text

        if vt in (ua.VariantType.String, ua.VariantType.ByteString, ua.VariantType.XmlElement):
            return text
        if vt == ua.VariantType.Boolean:
            return text.strip().lower() in ("1", "true", "yes", "on")

        if text == "":
            return None

        try:
            if vt in (ua.VariantType.Float, ua.VariantType.Double):
                return float(text)
            if vt in (
                ua.VariantType.SByte,
                ua.VariantType.Byte,
                ua.VariantType.Int16,
                ua.VariantType.UInt16,
                ua.VariantType.Int32,
                ua.VariantType.UInt32,
                ua.VariantType.Int64,
                ua.VariantType.UInt64,
            ):
                return int(float(text))
        except Exception:
            return None

        return text

    async def write_values(self, values: dict[str, dict[str, str]]) -> None:
        if not await self._connect_if_needed():
            return
        try:
            for sid, row in values.items():
                if sid not in self._station_map:
                    continue
                row_nodes = self._nodes.get(sid) or {}
                if not row_nodes:
                    continue
                for tag, node in row_nodes.items():
                    try:
                        raw = _format_datetime_str(str(row.get(tag, "")))
                        casted = await self._coerce_value(node, raw)
                        if casted is None:
                            continue
                        vt = await node.read_data_type_as_variant_type()
                        dv = ua.DataValue(
                            Value=ua.Variant(casted, vt),
                            SourceTimestamp=None,
                            ServerTimestamp=None,
                            SourcePicoseconds=None,
                            ServerPicoseconds=None,
                        )
                        await node.write_attribute(ua.AttributeIds.Value, dv)
                    except Exception as e:
                        self._report_error(f"write failed: sid={sid} tag={tag} err={e}")
                        await self._disconnect()
                        return
        except Exception as e:
            self._report_error(f"write loop failed: {e}")
            await self._disconnect()

    async def close(self) -> None:
        await self._disconnect()


def _station_ids(cfg: dict) -> list[str]:
    od = cfg.get("openData") or {}
    stations = od.get("stations") or []
    if isinstance(stations, list) and stations:
        return [str((s or {}).get("id") or "").strip() for s in stations if isinstance(s, dict)]
    return [str(x).strip() for x in (od.get("target") or []) if str(x).strip()]


def _fetch_values(cfg: dict, station_ids: list[str]) -> dict[str, dict[str, str]]:
    od = cfg.get("openData") or {}
    addr = str(od.get("address") or "").strip()
    api = str(od.get("api") or "").strip()
    auth = str(od.get("auth_key") or "").strip()
    ids = [x.strip() for x in station_ids if x.strip()]
    if not (addr and api and auth and ids):
        return {}

    url = f"{addr}{api}?Authorization={auth}&format=JSON&StationId={','.join(ids)}&WeatherElement=&GeoInfo=StationAltitude,CountyName"
    try:
        with request.urlopen(url, timeout=8) as resp:
            payload = json.load(resp)
        rows = payload.get("records", {}).get("Station", [])
    except Exception:
        return {}

    out: dict[str, dict[str, str]] = {}
    for s in rows:
        sid = str(s.get("StationId") or "").strip()
        if not sid:
            continue
        w = s.get("WeatherElement") or {}
        g = s.get("GeoInfo") or {}
        parts = [
            str(w.get("Now", {}).get("Precipitation", "")),
            str(w.get("DailyExtreme", {}).get("DailyLow", {}).get("TemperatureInfo", {}).get("AirTemperature", "")),
            str(w.get("SunshineDuration", "")),
            str(w.get("DailyExtreme", {}).get("DailyHigh", {}).get("TemperatureInfo", {}).get("AirTemperature", "")),
            str(g.get("StationAltitude", "")),
            str(w.get("Max10MinAverage", {}).get("Occurred_at", {}).get("WindDirection", "")),
            str(w.get("Max10MinAverage", {}).get("WindSpeed", "")),
            str(w.get("GustInfo", {}).get("PeakGustSpeed", "")),
            str(w.get("UVIndex", "")),
            str(w.get("GustInfo", {}).get("Occurred_at", {}).get("WindDirection", "")),
            str(w.get("RelativeHumidity", "")),
            str(w.get("AirPressure", "")),
            str(w.get("AirTemperature", "")),
            str(w.get("WindDirection", "")),
            str(w.get("WindSpeed", "")),
            str(g.get("CountyName", "")),
            str(w.get("DailyExtreme", {}).get("DailyLow", {}).get("TemperatureInfo", {}).get("Occurred_at", {}).get("DateTime", "")),
            str(w.get("DailyExtreme", {}).get("DailyHigh", {}).get("TemperatureInfo", {}).get("Occurred_at", {}).get("DateTime", "")),
            str(w.get("Max10MinAverage", {}).get("Occurred_at", {}).get("DateTime", "")),
            str(w.get("GustInfo", {}).get("Occurred_at", {}).get("DateTime", "")),
            str(w.get("VisibilityDescription", "")),
            str(w.get("Weather", "")),
        ]
        out[sid] = {k: v for k, v in zip(VALUE_TAGS, parts, strict=False)}
    return out


async def run_server(config_path: str) -> int:
    cfg = _load_config(config_path)
    host, port = _parse_url(str((cfg.get("opcUA") or {}).get("url") or "opc.tcp://127.0.0.1:48484"))

    server = Server()
    await server.init()
    endpoint = f"opc.tcp://{host}:{port}"
    server.set_endpoint(endpoint)
    server.set_server_name("OpenData Weather UA")
    server.set_security_policy([ua.SecurityPolicyType.NoSecurity])

    idx = await server.register_namespace("urn:opendata:weather")
    weather_obj = await server.nodes.objects.add_object(idx, "Weather")

    station_nodes: dict[str, dict[str, ua.NodeId]] = {}
    for sid in _station_ids(cfg):
        sid = sid.strip()
        if not sid:
            continue
        station_obj = await weather_obj.add_object(idx, sid)
        nodes: dict[str, ua.NodeId] = {}
        for tag in VALUE_TAGS:
            v = await station_obj.add_variable(idx, tag, "")
            await v.set_writable(False)
            nodes[tag] = v
        station_nodes[sid] = nodes

    # ── 從 config 讀取多筆 mirror endpoints ──────────────────────
    opc_cfg = cfg.get("opcUA") or {}
    mirror_station_map = opc_cfg.get("mirror_station_map") or DEFAULT_MIRROR_STATION_MAP
    mirror_endpoints_cfg = opc_cfg.get("mirror_endpoints") or []
    mirrors: list[_RemoteMirrorWriter] = [
_RemoteMirrorWriter(ep["url"], ep.get("name",""), mirror_station_map)
        for ep in mirror_endpoints_cfg
        if isinstance(ep, dict) and ep.get("url") and ep.get("enabled", True)
    ]

    stop_event = asyncio.Event()

    def _request_stop(*_args):
        stop_event.set()

    loop = asyncio.get_running_loop()
    if os.name != "nt":
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, _request_stop)

    # 需要抓取的 station IDs = 本機站 + 所有 mirror 的站
    all_mirror_ids: set[str] = set(mirror_station_map.keys())

    log_path = Path(config_path).parent / "server.log"

    def _log(msg: str) -> None:
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        line = f"[{ts}] {msg}"
        print(line, flush=True)
        try:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except Exception:
            pass

    station_count = len(station_nodes)
    mirror_count  = len(mirrors)
    _log(f"[OPC UA] Server 啟動 | Endpoint: {endpoint} | 測站: {station_count} | Mirror 端點: {mirror_count}")

    async with server:
        cycle         = 0
        mirror_cycle  = 0
        last_values: dict = {}
        last_weather_time = 0.0   # 上次氣象查詢的時間戳
        last_mirror_time  = 0.0   # 上次鏡像推送的時間戳

        while not stop_event.is_set():
            now = asyncio.get_event_loop().time()

            # ── 讀取最新頻率設定 ──────────────────────────────────
            try:
                fresh_cfg = _load_config(config_path)
                intervals = fresh_cfg.get("intervals") or {}
                weather_interval = int(intervals.get("weather_fetch_seconds") or 600)
                mirror_interval  = int(intervals.get("mirror_push_seconds")   or 600)
            except Exception:
                weather_interval = 600
                mirror_interval  = 600

            # ── 氣象資料查詢（依 weather_interval） ─────────────
            if now - last_weather_time >= weather_interval:
                cycle += 1
                all_ids = sorted(set(list(station_nodes.keys())) | all_mirror_ids)
                _log(f"[氣象查詢] 第 {cycle} 次 | 查詢 {len(all_ids)} 個測站 …")
                values = await asyncio.to_thread(_fetch_values, fresh_cfg, all_ids)
                if values:
                    last_values = values
                    _log(f"[氣象查詢] ✅ 取得 {len(values)} 筆資料 | 間隔: {weather_interval}s")
                else:
                    _log(f"[氣象查詢] ⚠️ 查詢失敗或無資料")

                # 寫入本機 OPC UA 節點
                for sid, tag_nodes in station_nodes.items():
                    row = last_values.get(sid, {})
                    for tag, node in tag_nodes.items():
                        try:
                            val = _format_datetime_str(str(row.get(tag, "")))
                            await node.write_value(val)
                        except Exception:
                            pass

                last_weather_time = now

            # ── 鏡像端點推送（依 mirror_interval，獨立計時） ─────
            if mirrors and last_values and (now - last_mirror_time >= mirror_interval):
                mirror_cycle += 1
                _log(f"[MIRROR] cycle={mirror_cycle} endpoints={mirror_count} interval={mirror_interval}s")
                for m in mirrors:
                    try:
                        await m.write_values(last_values)
                        _log(f"[MIRROR] OK name={m.name} endpoint={m.endpoint}")
                    except Exception as e:
                        _log(f"[MIRROR] FAIL name={m.name} endpoint={m.endpoint} error={e}")
                last_mirror_time = now

            # ── 短暫等待（每 5 秒檢查一次是否到達執行時間） ─────
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                pass

    for m in mirrors:
        await m.close()
    return 0


def main(config_path: str) -> int:
    try:
        return int(asyncio.run(run_server(config_path=config_path)))
    except KeyboardInterrupt:
        return 0
    except Exception:
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    if getattr(sys, "frozen", False):
        cfg = os.path.join(os.path.dirname(os.path.abspath(sys.executable)), "data", "config.json")
    else:
        cfg = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "config.json")
    raise SystemExit(main(config_path=cfg))
