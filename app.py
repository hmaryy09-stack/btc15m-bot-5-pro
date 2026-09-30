import math
import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import requests
from flask import Flask, jsonify, render_template_string

app = Flask(__name__)

NY = ZoneInfo("America/New_York")
SERIES = "KXBTC15M"

# Señal fija por vela
FIXED_SIGNALS = {}

HTTP_TIMEOUT = 8


# =========================================================
# DATOS
# =========================================================

def get_json(url, params=None):
    try:
        r = requests.get(
            url,
            params=params,
            timeout=HTTP_TIMEOUT
        )
        r.raise_for_status()
        return r.json()
    except Exception:
        return None


def parse_time(value):
    if value is None:
        return None

    try:
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(
                value,
                tz=timezone.utc
            )

        text = str(value).replace(
            "Z",
            "+00:00"
        )

        dt = datetime.fromisoformat(text)

        if dt.tzinfo is None:
            dt = dt.replace(
                tzinfo=timezone.utc
            )

        return dt.astimezone(timezone.utc)

    except Exception:
        return None


def get_markets():
    urls = [
        "https://external-api.kalshi.com/trade-api/v2/markets",
        "https://api.elections.kalshi.com/trade-api/v2/markets",
    ]

    for url in urls:
        data = get_json(
            url,
            {
                "series_ticker": SERIES,
                "status": "open",
                "limit": 100,
            },
        )

        if data and isinstance(
            data.get("markets"),
            list
        ):
            return data["markets"]

    return []


def get_current_market():
    now = datetime.now(timezone.utc)

    for market in get_markets():

        opened = parse_time(
            market.get("open_time")
        )

        closed = parse_time(
            market.get("close_time")
        )

        if (
            opened
            and closed
            and opened <= now < closed
        ):
            return market

    return None


def get_next_market():
    now = datetime.now(timezone.utc)
    candidates = []

    for market in get_markets():

        opened = parse_time(
            market.get("open_time")
        )

        if opened and opened > now:
            candidates.append(
                (opened, market)
            )

    if not candidates:
        return None

    candidates.sort(
        key=lambda item: item[0]
    )

    return candidates[0][1]


def get_target(market):
    if not market:
        return None

    for key in (
        "custom_strike",
        "strike",
        "floor_strike",
        "cap_strike",
    ):

        value = market.get(key)

        if value is not None:
            try:
                return float(value)
            except Exception:
                pass

    return None


def get_kalshi_probability(market):
    if not market:
        return None

    try:
        bid = market.get(
            "yes_bid_dollars"
        )

        ask = market.get(
            "yes_ask_dollars"
        )

        last = market.get(
            "last_price_dollars"
        )

        if (
            bid is not None
            and ask is not None
        ):
            return (
                float(bid)
                + float(ask)
            ) / 2

        if last is not None:
            return float(last)

    except Exception:
        pass

    try:
        bid = market.get("yes_bid")
        ask = market.get("yes_ask")
        last = market.get("last_price")

        if (
            bid is not None
            and ask is not None
        ):
            return (
                float(bid)
                + float(ask)
            ) / 200

        if last is not None:
            return float(last) / 100

    except Exception:
        pass

    return None


# =========================================================
# BITCOIN / KRAKEN
# =========================================================

def get_btc_price():

    data = get_json(
        "https://api.kraken.com/0/public/Ticker",
        {
            "pair": "XBTUSD"
        },
    )

    if not data:
        return None

    try:
        result = data.get(
            "result",
            {}
        )

        if not result:
            return None

        item = next(
            iter(result.values())
        )

        return float(
            item["c"][0]
        )

    except Exception:
        return None


def get_btc_candles():

    data = get_json(
        "https://api.kraken.com/0/public/OHLC",
        {
            "pair": "XBTUSD",
            "interval": 1,
        },
    )

    if not data:
        return []

    try:
        result = data.get(
            "result",
            {}
        )

        key = next(
            k for k in result
            if k != "last"
        )

        candles = []

        for row in result[key][-120:]:

            candles.append(
                {
                    "time": int(row[0]),
                    "open": float(row[1]),
                    "high": float(row[2]),
                    "low": float(row[3]),
                    "close": float(row[4]),
                    "volume": float(row[6]),
                }
            )

        return candles

    except Exception:
        return []


# =========================================================
# MODELO
# =========================================================

def calculate_model(
    candles,
    target=None
):

    if len(candles) < 16:

        return {
            "direction": "SUBE",
            "probability": 0.55,
            "confirmation": 1,
            "trend": "NEUTRAL",
            "momentum": "NEUTRO",
            "volatility": "MEDIA",
        }

    closes = [
        c["close"]
        for c in candles
    ]

    last = closes[-1]

    r1 = (
        last / closes[-2]
        - 1
    )

    r3 = (
        last / closes[-4]
        - 1
    )

    r5 = (
        last / closes[-6]
        - 1
    )

    r10 = (
        last / closes[-11]
        - 1
    )

    r15 = (
        last / closes[-16]
        - 1
    )

    momentum = (
        r1 * 0.30
        + r3 * 0.25
        + r5 * 0.20
        + r10 * 0.15
        + r15 * 0.10
    )

    recent = closes[-15:]

    average = (
        sum(recent)
        / len(recent)
    )

    trend_up = last >= average

    target_bias = 0

    if target:
        target_bias = (
            target - last
        ) / last

    score = momentum

    score += (
        max(
            -0.0008,
            min(
                0.0008,
                target_bias
            )
        )
        * 0.35
    )

    direction = (
        "SUBE"
        if score >= 0
        else "BAJA"
    )

    probability = (
        0.55
        + min(
            0.29,
            abs(score) * 900
        )
    )

    if probability > 0.84:
        probability = 0.84

    momentum_ok = (
        (
            direction == "SUBE"
            and momentum >= 0
        )
        or
        (
            direction == "BAJA"
            and momentum < 0
        )
    )

    trend_ok = (
        (
            direction == "SUBE"
            and trend_up
        )
        or
        (
            direction == "BAJA"
            and not trend_up
        )
    )

    target_ok = True

    if target:

        target_ok = (
            (
                direction == "SUBE"
                and target >= last
            )
            or
            (
                direction == "BAJA"
                and target <= last
            )
        )

    confirmation = (
        int(momentum_ok)
        + int(trend_ok)
        + int(target_ok)
    )

    trend = (
        "ALCISTA"
        if trend_up
        else "BAJISTA"
    )

    momentum_label = (
        "POSITIVO"
        if momentum >= 0
        else "NEGATIVO"
    )

    volatility_values = [
        abs(
            closes[i]
            / closes[i - 1]
            - 1
        )
        for i in range(
            1,
            len(closes)
        )
    ]

    avg_move = (
        sum(
            volatility_values[-20:]
        )
        / max(
            1,
            len(
                volatility_values[-20:]
            )
        )
    )

    if avg_move >= 0.0007:
        volatility = "ALTA"

    elif avg_move >= 0.00035:
        volatility = "MEDIA"

    else:
        volatility = "BAJA"

    return {
        "direction": direction,
        "probability": probability,
        "confirmation": confirmation,
        "trend": trend,
        "momentum": momentum_label,
        "volatility": volatility,
    }


# =========================================================
# SEÑAL FIJA
# =========================================================

def get_fixed_signal(market):

    if not market:
        return calculate_model([])

    ticker = market.get(
        "ticker",
        ""
    )

    opened = parse_time(
        market.get("open_time")
    )

    if not ticker or not opened:
        return calculate_model(
            get_btc_candles()
        )

    key = (
        ticker,
        int(
            opened.timestamp()
        ),
    )

    if key not in FIXED_SIGNALS:

        candles = get_btc_candles()

        target = get_target(
            market
        )

        FIXED_SIGNALS[key] = (
            calculate_model(
                candles,
                target
            )
        )

    return FIXED_SIGNALS[key]


# =========================================================
# CÁLCULOS
# =========================================================

def money(value):

    if value is None:
        return "—"

    return f"${value:,.2f}"


def pct(value):

    if value is None:
        return "—"

    return f"{value * 100:.0f}%"


def calculate_entry(probability):

    return probability / 1.10


def calculate_projection(
    btc,
    candles,
    seconds_left,
    direction
):

    if not btc or len(candles) < 6:
        return None, None, None

    closes = [
        c["close"]
        for c in candles
    ]

    current = closes[-1]

    moves = []

    for i in range(
        max(
            1,
            len(closes) - 10
        ),
        len(closes)
    ):

        moves.append(
            closes[i]
            - closes[i - 1]
        )

    if not moves:
        return None, None, None

    rate = (
        sum(moves)
        / len(moves)
        / 60
    )

    if direction == "BAJA":
        rate = -abs(rate)

    else:
        rate = abs(rate)

    projected = (
        current
        + rate * seconds_left
    )

    required = None

    return (
        projected,
        rate,
        required
    ) 
# =========================================================
# GRÁFICO
# =========================================================

def make_chart_svg(candles):

    if len(candles) < 2:
        return (
            '<div style="padding:30px;text-align:center;'
            'color:#8ea0bb;">Esperando datos...</div>'
        )

    data = candles[-60:]

    width = 900
    height = 320
    pad_x = 35
    pad_y = 25

    highs = [c["high"] for c in data]
    lows = [c["low"] for c in data]

    top = max(highs)
    bottom = min(lows)
    span = max(1, top - bottom)

    def x(i):
        return (
            pad_x
            + i * (width - pad_x * 2)
            / max(1, len(data) - 1)
        )

    def y(value):
        return (
            pad_y
            + (top - value)
            * (height - pad_y * 2)
            / span
        )

    parts = []

    for row in range(5):

        gy = (
            pad_y
            + row * (height - pad_y * 2) / 4
        )

        parts.append(
            f'<line x1="{pad_x}" y1="{gy:.1f}" '
            f'x2="{width-pad_x}" y2="{gy:.1f}" '
            f'class="grid"/>'
        )

    candle_width = max(
        5,
        (width - pad_x * 2)
        / len(data)
        * 0.55,
    )

    for i, c in enumerate(data):

        cx = x(i)

        yo = y(c["open"])
        yc = y(c["close"])
        yh = y(c["high"])
        yl = y(c["low"])

        up = c["close"] >= c["open"]
        cls = "up" if up else "down"

        body_top = min(yo, yc)
        body_h = max(
            2,
            abs(yc - yo)
        )

        parts.append(
            f'<line x1="{cx:.1f}" y1="{yh:.1f}" '
            f'x2="{cx:.1f}" y2="{yl:.1f}" '
            f'class="wick {cls}"/>'
        )

        parts.append(
            f'<rect x="{cx-candle_width/2:.1f}" '
            f'y="{body_top:.1f}" '
            f'width="{candle_width:.1f}" '
            f'height="{body_h:.1f}" '
            f'class="candle {cls}"/>'
        )

    current = data[-1]["close"]
    cy = y(current)

    parts.append(
        f'<line x1="{pad_x}" y1="{cy:.1f}" '
        f'x2="{width-pad_x}" y2="{cy:.1f}" '
        f'class="price-line"/>'
    )

    parts.append(
        f'<text x="{width-pad_x-5}" '
        f'y="{cy-7:.1f}" '
        f'class="price-label" '
        f'text-anchor="end">'
        f'{money(current)}</text>'
    )

    return (
        f'<svg viewBox="0 0 {width} {height}" '
        f'class="btc-chart" '
        f'preserveAspectRatio="none">'
        + "".join(parts)
        + "</svg>"
    )


# =========================================================
# HTML PRO
# =========================================================

HTML = r"""
<!doctype html>

<html lang="es">

<head>

<meta charset="utf-8">

<meta name="viewport"
content="width=device-width,initial-scale=1,viewport-fit=cover">

<meta http-equiv="refresh" content="10">

<title>BTC 15 MIN • Bot 5 PRO</title>

<style>

:root{
    --bg:#050914;
    --card:#081326;
    --card2:#0b1730;
    --cyan:#00d9ff;
    --green:#25f58a;
    --purple:#9d55ff;
    --red:#ff3e62;
    --yellow:#ffd43b;
    --text:#f5f8ff;
    --muted:#8ea0bb;
    --line:#183254;
}

*{
    box-sizing:border-box;
}

body{
    margin:0;
    color:var(--text);
    background:
      radial-gradient(
        circle at 15% 5%,
        #0d2440 0,
        transparent 35%
      ),
      radial-gradient(
        circle at 90% 15%,
        #20104a 0,
        transparent 35%
      ),
      linear-gradient(
        145deg,
        #03060d,
        #07101f 55%,
        #050914
      );

    font-family:
      -apple-system,
      BlinkMacSystemFont,
      "Segoe UI",
      Arial,
      sans-serif;
}

.page{
    width:min(1180px,94%);
    margin:18px auto 35px;
}

.header{
    border:1px solid #00aaff;
    border-radius:18px;
    padding:18px 20px;

    display:flex;
    align-items:center;
    justify-content:space-between;

    background:
      linear-gradient(
        120deg,
        #071629,
        #0a1830
      );

    box-shadow:
      0 0 28px
      rgba(0,170,255,.16);
}

.brand{
    display:flex;
    align-items:center;
    gap:14px;
}

.coin{
    width:55px;
    height:55px;
    border-radius:50%;

    display:grid;
    place-items:center;

    font-size:30px;
    font-weight:900;

    background:
      linear-gradient(
        145deg,
        #ffb000,
        #ff7a00
      );

    color:white;

    box-shadow:
      0 0 25px
      rgba(255,154,0,.28);
}

.title{
    font-size:25px;
    font-weight:900;
}

.subtitle{
    color:#9eb0c9;
    margin-top:3px;
}

.live{
    text-align:right;
    color:var(--green);
    font-weight:900;
}

.live small{
    display:block;
    color:#7f95b2;
    margin-top:4px;
}

.grid-top{
    display:grid;
    grid-template-columns:1.55fr .75fr;
    gap:14px;
    margin-top:14px;
}

.grid{
    display:grid;
    grid-template-columns:1fr 1fr;
    gap:14px;
    margin-top:14px;
}

.card{
    background:
      linear-gradient(
        145deg,
        rgba(9,23,45,.96),
        rgba(5,13,28,.96)
      );

    border:1px solid var(--line);
    border-radius:17px;

    padding:17px;

    box-shadow:
      0 10px 35px
      rgba(0,0,0,.25);
}

.green-border{
    border-color:#00e98a;

    box-shadow:
      0 0 25px
      rgba(0,255,145,.13);
}

.cyan-border{
    border-color:#00baff;
}

.purple-border{
    border-color:#8e45ff;
}

.card-title{
    color:#7eeaff;

    font-size:15px;
    font-weight:900;

    text-transform:uppercase;

    letter-spacing:.04em;

    margin-bottom:12px;
}

.direction{
    display:flex;
    align-items:center;
    justify-content:space-between;

    gap:12px;
}

.direction-name{
    font-size:46px;
    font-weight:1000;

    color:var(--green);

    line-height:1;
}

.direction-prob{
    font-size:42px;
    font-weight:1000;

    color:var(--green);
}

.lock{
    color:var(--yellow);

    margin-top:10px;

    font-size:14px;
}

.price{
    font-size:34px;
    font-weight:900;

    margin-top:8px;
}

.delta{
    color:var(--green);
    font-weight:800;

    margin-top:4px;
}

.metric{
    font-size:28px;
    font-weight:900;
}

.muted{
    color:var(--muted);
    font-size:13px;
}

.chart-card{
    margin-top:14px;
    border-color:#8145ff;
}

.chart-head{
    display:flex;
    justify-content:space-between;
    align-items:center;

    gap:10px;
}

.pills{
    display:flex;
    gap:4px;
}

.pill{
    border:1px solid #4c42a4;

    padding:7px 12px;

    border-radius:10px;

    color:#aeb8d2;

    font-size:12px;
}

.pill.active{
    background:#7139e8;
    color:white;

    box-shadow:
      0 0 18px
      rgba(125,62,255,.35);
}

.btc-chart{
    width:100%;
    height:340px;

    margin-top:10px;

    background:
      rgba(0,0,0,.13);

    border-radius:12px;
}

.grid{
    stroke:#173052;
    stroke-width:1;
}

.wick.up,
.candle.up{
    stroke:#16e58a;
    fill:#16e58a;
}

.wick.down,
.candle.down{
    stroke:#ff3d62;
    fill:#ff3d62;
}

.wick{
    stroke-width:2;
}

.price-line{
    stroke:#24f58d;
    stroke-width:1.5;

    stroke-dasharray:5 5;
}

.price-label{
    fill:#8dffbf;
    font-size:15px;
    font-weight:800;
}

.three{
    display:grid;
    grid-template-columns:
      repeat(3,1fr);

    gap:10px;
}

.mini{
    background:#07172b;

    border:1px solid #17446a;

    border-radius:13px;

    padding:13px;
}

.mini strong{
    display:block;

    margin-top:5px;

    color:#22f58b;
}

.live-bar{
    display:flex;

    height:16px;

    overflow:hidden;

    border-radius:999px;

    margin-top:12px;

    background:#142238;
}

.live-up{
    background:#21ef88;
}

.live-down{
    background:#ff385d;
}

.live-values{
    display:flex;

    justify-content:space-between;

    margin-top:8px;

    font-weight:900;
}

.up-text{
    color:#27f58d;
}

.down-text{
    color:#ff5572;
}

.two{
    display:grid;
    grid-template-columns:1fr 1fr;

    gap:14px;
}

.big-green{
    color:#22f58b;

    font-size:24px;

    font-weight:900;
}

.info-row{
    display:flex;

    justify-content:space-between;

    gap:15px;

    padding:8px 0;

    border-bottom:
      1px solid
      rgba(40,70,110,.45);
}

.info-row:last-child{
    border-bottom:0;
}

.alert{
    padding:13px;

    border-radius:12px;

    border:1px solid #13e38b;

    background:
      rgba(0,220,125,.07);

    color:#6dffb1;

    font-weight:800;
}

.footer{
    margin-top:14px;

    display:flex;

    justify-content:space-between;

    gap:10px;

    padding:12px 15px;

    border:1px solid #1a3b61;

    border-radius:13px;

    color:#a9b9cf;

    font-size:12px;
}

@media(max-width:800px){

    .grid-top,
    .grid,
    .two{
        grid-template-columns:1fr;
    }

    .three{
        grid-template-columns:1fr;
    }

    .title{
        font-size:21px;
    }

    .direction-name{
        font-size:39px;
    }

    .direction-prob{
        font-size:35px;
    }

    .btc-chart{
        height:260px;
    }
}

</style>

</head>

<body>

<div class="page">


<section class="header">

    <div class="brand">

        <div class="coin">
            ₿
        </div>

        <div>

            <div class="title">
                BTC • 15 MIN
            </div>

            <div class="subtitle">
                Bot 5 • Predictor • Kalshi
            </div>

        </div>

    </div>


    <div class="live">

        🟢 EN VIVO

        <small>
            {{ now }}
        </small>

    </div>

</section>


<section class="grid-top">


<div class="card green-border">

    <div class="card-title">
        🔒 LECTURA ACTUAL — FIJA
    </div>


    <div class="direction">

        <div class="direction-name">

            {% if direction == "SUBE" %}
                ▲ SUBE
            {% else %}
                ▼ BAJA
            {% endif %}

        </div>


        <div class="direction-prob">
            {{ probability }}
        </div>

    </div>


    <div class="muted">
        probabilidad del modelo
    </div>


    <div class="lock">
        🔒 Dirección fija durante esta vela
    </div>


    <div class="price">
        {{ btc_price }}
    </div>


    <div class="delta">
        {{ btc_change }}
    </div>

</div>


<div>


<div class="card purple-border">

    <div class="card-title">
        🎯 OBJETIVO KALSHI
    </div>

    <div class="metric">
        {{ target }}
    </div>

    <div class="delta">
        Distancia: {{ target_distance }}
    </div>

</div>


<div
class="card cyan-border"
style="margin-top:14px;"
>

    <div class="card-title">
        ⏱️ CIERRE
    </div>

    <div class="metric">
        {{ countdown }}
    </div>

    <div class="muted">
        Cierre: {{ close_time }}
    </div>

</div>


</div>

</section>


<section class="card chart-card">

    <div class="chart-head">

        <div class="card-title">
            📈 GRÁFICO BTC
        </div>


        <div class="pills">

            <div class="pill">
                1m
            </div>

            <div class="pill">
                5m
            </div>

            <div class="pill active">
                15m
            </div>

            <div class="pill">
                1h
            </div>

        </div>

    </div>


    {{ chart|safe }}

</section>


<section class="card cyan-border">

    <div class="card-title">
        🛡️ DIRECCIÓN Y CONFIRMACIÓN
    </div>


    <div class="direction">

        <div>

            <div class="big-green">

                {% if direction == "SUBE" %}
                    ▲ SUBE
                {% else %}
                    ▼ BAJA
                {% endif %}

                {{ probability }}

            </div>


            <div class="progress">

                <div
                style="width:{{ probability }};"
                ></div>

            </div>

        </div>


        <div class="metric">
            {{ confirmation }}/3
        </div>

    </div>


    <div
    class="three"
    style="margin-top:13px;"
    >

        <div class="mini">

            Tendencia

            <strong>
                {{ trend }}
            </strong>

        </div>


        <div class="mini">

            Momentum

            <strong>
                {{ momentum }}
            </strong>

        </div>


        <div class="mini">

            Volatilidad

            <strong
            style="color:#ffd43b;"
            >
                {{ volatility }}
            </strong>

        </div>

    </div>

</section>


<section class="card purple-border">

    <div class="card-title">
        📊 KALSHI EN VIVO
    </div>


    <div class="live-values">

        <span class="up-text">
            🟢 SUBE {{ kalshi_up }}
        </span>

        <span class="down-text">
            🔴 BAJA {{ kalshi_down }}
        </span>

    </div>


    <div class="live-bar">

        <div
        class="live-up"
        style="width:{{ kalshi_up }};"
        ></div>

        <div
        class="live-down"
        style="width:{{ kalshi_down }};"
        ></div>

    </div>


    <div
    class="muted"
    style="margin-top:8px;"
    >

        Dirección en vivo:
        <b>{{ kalshi_direction }}</b>

        — puede cambiar durante la vela.

    </div>

</section>


<section class="card cyan-border">

    <div class="card-title">
        🎯 GUÍA PARA EL CIERRE
    </div>


    <div class="big-green">

        Probable cierre {{ direction }}

        <span style="float:right;">
            {{ probability }}
        </span>

    </div>


    <div
    class="muted"
    style="margin-top:5px;"
    >

        Basado en precio,
        movimiento reciente
        y ritmo actual.

    </div>


    <div
    class="three"
    style="margin-top:13px;"
    >

        <div class="mini">

            Cierre proyectado

            <strong>
                {{ projection }}
            </strong>

        </div>


        <div class="mini">

            Ritmo actual

            <strong>
                {{ rate }}
            </strong>

        </div>


        <div class="mini">

            Ritmo necesario

            <strong>
                {{ needed }}
            </strong>

        </div>

    </div>

</section>


<section class="card">

    <div class="three">

        <div class="mini">

            ⚡ Entrada máx.

            <strong>
                {{ entry }}
            </strong>

        </div>


        <div class="mini">

            🥧 Posición sugerida

            <strong>
                25%
            </strong>

        </div>


        <div class="mini">

            ⏳ Próxima vela

            <strong>
                {{ next_time }}
            </strong>

        </div>

    </div>

</section>


<section class="two">


<div class="card cyan-border">

    <div class="card-title">
        🔎 MODELO VS KALSHI
    </div>


    <div class="info-row">

        <span>
            Modelo
        </span>

        <b>
            {{ direction }}
            {{ probability }}
        </b>

    </div>


    <div class="info-row">

        <span>
            Kalshi a favor
        </span>

        <b>
            {{ kalshi_favor }}
        </b>

    </div>


    <div class="info-row">

        <span>
            Diferencia
        </span>

        <b>
            {{ difference }}
        </b>

    </div>

</div>


<div class="card purple-border">

    <div class="card-title">
        🚨 RADAR
    </div>


    <div class="alert">
        {{ radar }}
    </div>


    <div
    class="muted"
    style="margin-top:10px;"
    >

        La señal principal permanece
        bloqueada hasta el cierre
        de esta vela.

    </div>

</div>


</section>


<section class="card">

    <div class="card-title">
        🔐 ESTADO DE LA VELA
    </div>


    <div class="info-row">

        <span>
            Ticker Kalshi
        </span>

        <b>
            {{ ticker }}
        </b>

    </div>


    <div class="info-row">

        <span>
            Inicio
        </span>

        <b>
            {{ open_time }}
        </b>

    </div>


    <div class="info-row">

        <span>
            Cierre
        </span>

        <b>
            {{ close_time }}
        </b>

    </div>


    <div class="info-row">

        <span>
            Dirección bloqueada
        </span>

        <b class="up-text">
            {{ direction }}
        </b>

    </div>

</section>


<div class="footer">

    <span>
        🇺🇸 Hora: Nueva York
    </span>

    <span>
        ℹ️ Solo señales
    </span>

    <span>
        ↻ Actualiza cada 10s
    </span>

</div>


</div>

</body>

</html>
"""


# =========================================================
# PREPARAR DATOS
# =========================================================

def build_context():

    market = get_current_market()

    next_market = get_next_market()

    btc = get_btc_price()

    candles = get_btc_candles()

    target_value = get_target(market)

    kalshi_value = get_kalshi_probability(
        market
    )

    signal = get_fixed_signal(
        market
    )

    direction = signal["direction"]

    probability_value = signal[
        "probability"
    ]

    if market:

        opened = parse_time(
            market.get("open_time")
        )

        closed = parse_time(
            market.get("close_time")
        )

    else:

        opened = None
        closed = None

    now_utc = datetime.now(
        timezone.utc
    )

    now_ny = now_utc.astimezone(
        NY
    )

    if closed:

        seconds_left = max(
            0,
            int(
                (
                    closed
                    - now_utc
                ).total_seconds()
            )
        )

    else:

        seconds_left = 0


    mins = seconds_left // 60

    secs = seconds_left % 60

    countdown = (
        f"{mins:02d}:{secs:02d}"
    )


    if closed:

        close_ny = closed.astimezone(
            NY
        )

        close_time = (
            close_ny.strftime(
                "%-I:%M %p NY"
            )
        )

    else:

        close_time = "—"


    if opened:

        open_time = (
            opened.astimezone(
                NY
            ).strftime(
                "%-I:%M %p NY"
            )
        )

    else:

        open_time = "—"


    ticker = (
        market.get("ticker")
        if market
        else "KXBTC15M"
    )


    btc_price = money(btc)

    btc_change = ""


    if (
        len(candles) >= 2
        and btc
    ):

        previous = (
            candles[-2]["close"]
        )

        if previous:

            change = (
                btc - previous
            )

            change_pct = (
                change
                / previous
                * 100
            )

            btc_change = (
                f"{change_pct:+.2f}% "
                f"({change:+.2f})"
            )


    if not btc_change:

        btc_change = (
            "Actualizando..."
        )


    if btc and target_value:

        dist = (
            (target_value - btc)
            / btc
            * 100
        )

        target_distance = (
            f"{dist:+.2f}%"
        )

    else:

        target_distance = "—"


    if kalshi_value is None:

        kalshi_value = 0.5


    kalshi_value = max(
        0,
        min(
            1,
            kalshi_value
        )
    )


    kalshi_up = pct(
        kalshi_value
    )

    kalshi_down = pct(
        1 - kalshi_value
    )


    kalshi_direction = (
        "SUBE"
        if kalshi_value >= 0.5
        else "BAJA"
    )


    if direction == "SUBE":

        kalshi_favor_value = (
            kalshi_value
        )

    else:

        kalshi_favor_value = (
            1 - kalshi_value
        )


    difference_value = abs(
        probability_value
        - kalshi_favor_value
    )


    difference = pct(
        difference_value
    )


    if difference_value >= 0.15:

        radar = (
            "⚠️ Kalshi está "
            "bastante separado "
            "del modelo."
        )

    elif difference_value >= 0.08:

        radar = (
            "👀 Hay diferencia "
            "entre modelo y Kalshi."
        )

    else:

        radar = (
            "✅ Modelo y Kalshi "
            "están relativamente "
            "alineados."
        )


    projection, rate, needed = (
        calculate_projection(
            btc,
            candles,
            seconds_left,
            direction
        )
    )


    if projection:

        projection_text = money(
            projection
        )

    else:

        projection_text = "—"


    if rate is not None:

        rate_text = (
            f"{rate:+.2f} $/s"
        )

    else:

        rate_text = "—"


    if (
        target_value
        and btc
        and seconds_left > 0
    ):

        delta = (
            target_value - btc
        )

        needed_rate = (
            delta
            / seconds_left
        )

        if direction == "BAJA":

            needed_rate = (
                -abs(needed_rate)
            )

        needed_text = (
            f"{needed_rate:+.2f} $/s"
        )

    else:

        needed_text = "—"


    entry = calculate_entry(
        probability_value
    )


    if next_market:

        next_open = parse_time(
            next_market.get(
                "open_time"
            )
        )

        if next_open:

            next_time = (
                next_open
                .astimezone(NY)
                .strftime(
                    "%-I:%M %p"
                )
            )

        else:

            next_time = "—"

    else:

        if closed:

            next_time = (
                closed
                .astimezone(NY)
                .strftime(
                    "%-I:%M %p"
                )
            )

        else:

            next_time = "—"


    return {

        "now":
            now_ny.strftime(
                "%-I:%M:%S %p NY"
            ),

        "direction":
            direction,

        "probability":
            pct(
                probability_value
            ),

        "btc_price":
            btc_price,

        "btc_change":
            btc_change,

        "target":
            money(
                target_value
            ),

        "target_distance":
            target_distance,

        "countdown":
            countdown,

        "close_time":
            close_time,

        "open_time":
            open_time,

        "ticker":
            ticker,

        "confirmation":
            signal[
                "confirmation"
            ],

        "trend":
            signal["trend"],

        "momentum":
            signal["momentum"],

        "volatility":
            signal["volatility"],

        "kalshi_up":
            kalshi_up,

        "kalshi_down":
            kalshi_down,

        "kalshi_direction":
            kalshi_direction,

        "kalshi_favor":
            pct(
                kalshi_favor_value
            ),

        "difference":
            difference,

        "radar":
            radar,

        "projection":
            projection_text,

        "rate":
            rate_text,

        "needed":
            needed_text,

        "entry":
            money(
                btc * entry
                if btc
                else None
            ),

        "next_time":
            next_time,

        "chart":
            make_chart_svg(
                candles
            ),
    }


# =========================================================
# RUTAS
# =========================================================

@app.route("/")
def home():

    context = build_context()

    return render_template_string(
        HTML,
        **context
    )


@app.route("/health")
def health():

    return jsonify(
        {
            "ok": True,
            "bot":
                "btc15m-bot-5-pro",
            "series":
                SERIES,
            "signals_only":
                True,
            "timestamp":
                datetime.now(
                    timezone.utc
                ).isoformat(),
        }
    )


if __name__ == "__main__":

    app.run(
        host="0.0.0.0",
        port=8080,
        debug=False
    )
