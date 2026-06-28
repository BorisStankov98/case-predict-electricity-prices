"""
build_report.py — събира всеки резултатен PNG и го запича в ЕДНА самостоятелна
HTML страница (изображенията са base64-вградени, без счупени връзки, отваря се
офлайн навсякъде).

За хоризонта **1d** (24ч) страницата не е просто галерия от фигури, а разказва
цялата история на Layer 1 на български — *какво изследваме → кои данни → кой
метод → какви резултати → извод* — с фигурите, вградени на правилните места.
Останалите хоризонти (15min, 1week) остават като подредени фигури.

Чете:    s3://<bucket>/data/results/**/*.png   (качени от model builder-ите),
         а ако S3 не е конфигуриран — локалните ./results/**/*.png.
Записва:  ./results/index.html                  (локално, винаги)
Качва:    s3://<bucket>/data/results/index.html (по подразбиране; --local за пропускане)

Страницата е вертикална (скролва се надолу) — НЕ е странично-скролваща.

Употреба:
    python build_report.py             # построй + качи страницата в data/results/ (S3 по подразбиране)
    python build_report.py --no-upload # фигурите се четат от S3, но index.html се пише само локално (preview)
    python build_report.py --local     # чете фигурите от local_store/ и пише само локално (без S3)
"""
import base64
import html
import sys
from datetime import datetime, timezone
from pathlib import Path

# Прави споделената tools/ папка importable (за upload_s3) от model/.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

RESULTS_PREFIX = "data/results/"
RESULTS_DIR = Path(__file__).parent / "results"
OUT_NAME = "index.html"

# Дружелюбни заглавия на секциите по хоризонт и реда, в който се появяват.
SECTIONS = [
    ("1d", "Day-ahead (24ч) — 4 модела vs ЕСО / naive"),
    ("1week", "Week-ahead (168ч) — 4 модела vs naive"),
    ("15min", "1ч-напред nowcast (→ 15-мин ramp) — 4 модела vs persistence"),
]
# Подреждане на фигурите в секция (по префикс); останалите следват по азбучен ред.
# pipeline_corr е веднага след pipeline_significant (корелациите по модел).
FIG_ORDER = ["pipeline_metrics", "pipeline_significant", "pipeline_corr",
             "pipeline_selection", "pipeline_diagnostics", "pipeline_intervals",
             "pipeline_learning_curve", "pipeline_final", "pipeline_15min_ramp"]

# Фигури, премахнати от отчета по хоризонт (по префикс на името).
PLAIN_EXCLUDE = {
    "1week": ("pipeline_learning_curve",),               # без learning curve (Ridge)
    "15min": ("pipeline_learning_curve", "pipeline_15min_ramp"),  # без learning curve и 15-мин рампа
}


# ── събиране на фигурите: S3, иначе локално ─────────────────────────────────
def collect() -> tuple[dict[str, dict[str, str]], str]:
    """Връща {horizon: {figure_name: base64_data_uri}} и етикет за източника."""
    out: dict[str, dict[str, str]] = {}

    # 1) активният backend (s3 или local) — четем през upload_s3
    try:
        from upload_s3 import describe_backend, list_keys, read_bytes  # noqa: PLC0415
        keys = list(list_keys(RESULTS_PREFIX, ".png"))
        if keys:
            for k in keys:
                rest = k[len(RESULTS_PREFIX):].split("/")
                if len(rest) < 2:
                    continue
                hz, name = rest[0], rest[-1]
                uri = "data:image/png;base64," + base64.b64encode(read_bytes(k)).decode("ascii")
                out.setdefault(hz, {})[name] = uri
            print(f"намерени {sum(len(v) for v in out.values())} PNG "
                  f"({len(out)} хоризонта) · backend: {describe_backend()}")
            return out, describe_backend()
    except Exception as e:  # noqa: BLE001
        print(f"  (backend не е наличен — минавам на локалната ./results/: {e})")

    # 2) резервен вариант — работната папка model/results/<hz>/*.png
    for png in sorted(RESULTS_DIR.glob("*/*.png")):
        hz, name = png.parent.name, png.name
        uri = "data:image/png;base64," + base64.b64encode(png.read_bytes()).decode("ascii")
        out.setdefault(hz, {})[name] = uri
    print(f"намерени {sum(len(v) for v in out.values())} локални PNG "
          f"({len(out)} хоризонта)")
    return out, "локални ./results/"


# ── малки HTML помощници ────────────────────────────────────────────────────
def fig_sort_key(name: str):
    for i, pref in enumerate(FIG_ORDER):
        if name.startswith(pref):
            return (i, name)
    return (len(FIG_ORDER), name)


def figure(uri: str | None, name: str, caption: str = "") -> str:
    """Една фигура-картичка — същата структура като досегашния отчет."""
    cap = html.escape(caption) if caption else html.escape(name)
    if not uri:
        return (f'<div class="missing">⚠ Липсва фигурата „{html.escape(name)}“ — '
                f'стартирай (run) съответния model builder (S3 по подразбиране).</div>')
    return (f'<figure><figcaption>{cap}</figcaption>'
            f'<img alt="{html.escape(name)}" src="{uri}"></figure>')


def ul(items: list[str]) -> str:
    return "<ul>" + "".join(f"<li>{x}</li>" for x in items) + "</ul>"


def table(headers: list[str], rows: list[list[str]]) -> str:
    th = "".join(f"<th>{h}</th>" for h in headers)
    trs = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows)
    return f'<table><thead><tr>{th}</tr></thead><tbody>{trs}</tbody></table>'


def takeaway(t: str) -> str:
    return f'<div class="takeaway"><b>Извод:</b> {t}</div>'


def note(t: str) -> str:
    return f'<div class="note">{t}</div>'


def step(title: str, body: str) -> str:
    """Един разказвателен блок (заглавие + съдържание), вертикално."""
    return f'<div class="step"><h3>{title}</h3>{body}</div>'


# ── разказът за 1d (24ч) ─────────────────────────────────────────────────────
def render_1d(figs: dict[str, str]) -> tuple[str, set[str]]:
    """HTML за 1d секцията + множеството имена на вече използваните фигури."""
    used: set[str] = set()

    def f(name: str, caption: str) -> str:
        used.add(name)
        return figure(figs.get(name), name, caption)

    parts: list[str] = []
    parts.append('<section id="layer1"><h2>Layer 1 — Прогноза на потреблението (товара) · 24ч — Константин Георгиев</h2>')

    parts.append(step("0 · Цел и принципи", ul([
        "<b>Цел:</b> прогноза на товара (MW) за <b>ден напред (24ч)</b>, плюс 1 седмица и 15 мин.",
        "<b>Бенчмарки:</b> официалната day-ahead прогноза на <b>ЕСО</b> и <b>naive</b> (предния ден).",
        "<b>Без look-ahead:</b> всеки feature ползва само налична към момента информация.",
    ])))

    parts.append(step("1 · Данни (източници)", table(["данни", "роля"], [
        ["<code>load_actual</code> (ENTSO-E)", "<b>таргет</b> — реалният товар"],
        ["<code>load_forecast_day_ahead</code> (ЕСО)", "<b>бенчмарк</b> (НЕ е feature)"],
        ["<code>bulgaria_1day_ahead_forecast</code> (open-meteo)", "<b>реална day-ahead метео-ПРОГНОЗА</b> за час T"],
        ["<code>days_off_bg</code>", "празници / уикенди"],
    ]) + note("Канонизирани в обща часова решетка, <b>локално българско време</b> "
              "(Europe/Sofia, tz-aware), от единен възпроизводим builder.")))

    parts.append(step("4 · Feature engineering",
        '<div class="cards">'
        '<div class="card"><h4>LOAD</h4>' + ul([
            "лагове <code>lag24/48/168/336/720/8760</code>",
            "rolling средни <code>roll24/168</code>",
            "разлики <code>diff_24_48/168</code>",
        ]) + '</div>'
        '<div class="card"><h4>WEATHER (прогноза за T)</h4>' + ul([
            "<code>temp</code>, <code>temp²</code>, <code>HDD/CDD</code>",
            "ветрове, <code>ghi/dni</code>, облачност, валеж, влажност",
            "интеракции с тип-ден",
        ]) + '</div>'
        '<div class="card"><h4>CALENDAR</h4>' + ul([
            "3 часови хармоники × работен ден",
            "ден-от-седмица, ден-от-годината",
            "<code>is_day_off</code>, празнични (pre/post/мост/Великден)",
        ]) + '</div></div>' +
        note("Всички блокове са <b>честни</b> — нито един feature не „надниква“ в бъдещето.")))

    parts.append(step("6 · Модели", ul([
        "<b>Ridge, Lasso, ElasticNet</b> (линейни, L2 / L1 / L1+L2 регуляризация) и <b>XGBoost</b> (дървета).",
        "ЕСО прогнозата <b>НЕ влиза като feature</b> никъде.",
        "Линейните се стандартизират; регуляризацията се избира с вътрешен CV (без да се пипа тестът).",
    ])))

    parts.append(step("7 · Walk-forward оценка (сетъп)", ul([
        "<b>Плъзгащ ~589-дневен прозорец</b> (≈1.5–2 г.), <b>15-дневни тестови блокове</b>.",
        "<b>Тестов период:</b> октомври 2025 → юни 2026 (~9 месеца, включва пълна зима).",
    ])))

    parts.append(step("8 · Метрики и резултати — 24ч (36 features)",
        f("pipeline_metrics.png", "L1 24ч — метрики на 4 модела vs ЕСО/naive") +
        takeaway("всички модели <b>бият ЕСО с ~29%</b> и naive с ~33%. XGBoost има най-добро MAPE, "
                 "но <b>най-голям overfit</b> (1.51 vs ~1.23 на линейните).")))

    parts.append(step("9–10 · Статистическа валидация и диагностика на грешките",
        f("pipeline_diagnostics.png", "Значимост (DM) + диагностика на грешките") +
        ul([
            "<b>Diebold-Mariano</b> (HAC) + moving-block bootstrap: всички модели <b>значимо бият ЕСО И "
            "naive</b> (p≈0).",
            "<b>Mincer-Zarnowitz:</b> нашите модели минават теста за рационалност; <b>ЕСО се проваля</b> "
            "(систематично изместена).",
            "Грешки: ACF lag1≈0.84, но lag24/168≈0 (присъщо на 24ч), <b>хетероскедастични</b>, "
            "<b>не-нормални</b>, но <b>стационарни</b> (ADF+KPSS).",
        ])))

    # Корелации по модел (Lasso · Ridge · XGBoost) — веднага след 9–10.
    parts.append(step("Корелации по модел (Lasso · Ridge · XGBoost)",
        f("pipeline_corr_Lasso.png", "Корелации feature→товар и feature×feature (Lasso)") +
        f("pipeline_corr_Ridge.png", "Корелации feature→товар и feature×feature (Ridge)") +
        f("pipeline_corr_XGBoost.png", "Корелации feature→товар и feature×feature (XGBoost)")))

    parts.append(step("11 · Подбор на features",
        f("pipeline_significant.png", "Значими / важни features за всеки модел") +
        f("pipeline_corr_ElasticNet.png", "Корелации feature→товар и feature×feature (ElasticNet)")))

    parts.append(step("12 · Доверителни интервали — Mondrian conformal (90%)",
        f("pipeline_intervals.png", "90% Mondrian покритие / ширина / Winkler") +
        ul([
            "Split-conformal, <b>отделен квантил на грешката по час</b> (Mondrian), distribution-free.",
            "Покритие <b>~89%</b> (цел 90%), <b>адаптивна ширина</b> (обед ±~440 MW, нощ ±~260 MW).",
            "<b>Winkler:</b> Ridge най-добър (най-тесни); XGBoost най-лош (овърфитът → по-широки).",
        ]) +
        takeaway("интервалите са калибрирани и адаптивни — лекуват хетероскедастичността.")))

    parts.append(step('13 · Избор на модел → <span class="pick">ElasticNet</span>',
        f("pipeline_selection.png", "Композитен резултат (MAPE↓ + overfit↓ + Winkler↓) → избран модел") +
        takeaway("за day-ahead товар <b>регуляризиран линеен модел</b> е най-балансираният избор; "
                 "XGBoost е последен — въпреки най-доброто MAPE, най-висок overfit + най-лоши интервали го свалят.")))

    parts.append(step("Финален модел — ElasticNet (24ч)",
        f("pipeline_final_ElasticNet.png",
          "scatter(adjR²) · хистограма грешки · actual/predicted · 90% Mondrian") +
        note("Четирите панела на избрания модел върху тестовия период.")))

    parts.append(step("14 · Другите хоризонти",
        table(["хоризонт", "ключово", "резултат"], [
            ["<b>1 седмица (168ч)</b>", "метео НЕ е налична 7 дни напред → <code>lag168</code> proxy (слаб)",
             "Ridge/ElasticNet ~282 MAE, <b>−19% vs naive</b>; XGBoost овърфитва; MAPE ~6%"],
            ["<b>15 минути</b>", "няма реален 15-мин товар → <b>mean-preserving дезагрегация</b>",
             "часово-агрегирано MAE = часовия модел (≈142); под-часовото е синтетично"],
        ]) +
        takeaway("колкото по-слаб сигналът (1 седм), толкова повече линейните бият дървото.")))

    # Останалите 1d фигури, които не са показани в разказа — приложение.
    # (learning curve е премахната напълно — изключваме я и от приложението.)
    extras = [n for n in sorted(figs, key=fig_sort_key)
              if n not in used and not n.startswith("pipeline_learning_curve")]
    if extras:
        body = "".join(figure(figs[n], n) for n in extras)
        parts.append(step("Приложение · Допълнителни 1d фигури", body))

    # Ключови изводи — обобщение на Layer 1, най-отдолу.
    parts.append(step("Ключови изводи",
        '<ol class="keys">'
        '<li><b>Реалната метео-прогноза е решаващата стъпка</b>.</li>'
        '<li><b>Линеен модел (ElasticNet) е най-балансираният</b> избор; XGBoost овърфитва.</li>'
        '<li><b>Грешките не са бял шум</b> (присъщо на 24ч), хетероскедастични и не-нормални → <b>Mondrian conformal</b>.</li>'
        '<li><b>Три хоризонта покрити:</b> 24ч (силен), 1 седм (+19% над naive), 15 мин (дезагрегация).</li>'
        '</ol>'))

    parts.append("</section>")
    return "\n".join(parts), used


# ── обикновена секция (15min, 1week): подредени фигури, вертикално ───────────
def render_plain(title: str, figs: dict[str, str], hz: str = "") -> str:
    skip = PLAIN_EXCLUDE.get(hz, ())
    parts = [f'<section><h2>{html.escape(title)}</h2>']
    for name in sorted(figs, key=fig_sort_key):
        if any(name.startswith(p) for p in skip):
            continue
        parts.append(figure(figs[name], name))
    parts.append("</section>")
    return "\n".join(parts)


# ── Layer 2 (supply): кратък разказ + фигурите на supply модела ──────────────
# Подредба и български надписи на supply фигурите.
SUPPLY_FIGS = [
    ("supply_series.png", "Пълна серия на предлагането (генерация + нетен внос), MW · локално BG време"),
    ("supply_predictions.png", "Тестов период: реално предлагане vs 6-те модела + naive (стандартизирано)"),
    ("supply_metrics.png", "Тестови метрики по модел — MSE / RMSE / MAE / MAPE (стандартизирани единици)"),
]


def render_layer2(figs: dict[str, str]) -> str:
    """HTML за Layer 2 (предлагане) — разказ + фигури."""
    parts: list[str] = ['<section id="layer2"><h2>Layer 2 — Прогноза на предлагането (supply) · 24ч — Камелия Косекова</h2>']
    parts.append(
        '<p class="lead"><b>Предлагане = обща генерация (всички производствени типове) '
        '+ нетен внос</b> (<code>net_position</code>). Целта е прогноза на предлагането от '
        '<b>честни</b> предиктори, налични към момента: <b>метео</b>, <b>календар</b> '
        '(уикенд/празник) и <b>недостъпност</b> (планов ремонт + аварийни изключвания). '
        'Същите етапи като Layer 1, разделени по слой: '
        '<code>transform&nbsp;→&nbsp;features&nbsp;→&nbsp;model</code>.</p>')

    parts.append(step("Данни и таргет", ul([
        "<b>Таргет:</b> <code>supply</code> = Σ генерация по тип + <code>net_position</code> (ENTSO-E).",
        "<b>Метео:</b> 9 актуални променливи (температура, вятър, радиация, облачност, валеж, влажност).",
        "<b>Календар:</b> <code>is_weekend</code>, <code>is_holiday</code>.",
        "<b>Недостъпност:</b> <code>prod_maint</code>, <code>gen_maint</code> (планов ремонт) и "
        "<code>gen_outages</code> (аварийни) — изведени от ENTSO-E unavailability върху часовата решетка.",
    ]) + note("Канонична часова решетка в локално BG време (Europe/Sofia, tz-aware), от единен builder "
              "(<code>transform_supply_master.py</code>).")))

    parts.append(step("Модели и оценка", ul([
        "<b>6 модела:</b> Lasso, Ridge, ElasticNet (линейни) + Decision Tree, Random Forest, "
        "Gradient Boosting (дървета), плюс <b>naive</b> (последна тренировъчна стойност).",
        "<b>Сплит:</b> train < 2025-10-01, test до края (хронологичен).",
        "Feature-ите и таргетът се <b>стандартизират</b> по сплит; метриките са в стандартизирани единици.",
    ])))

    for name, cap in SUPPLY_FIGS:
        parts.append(figure(figs.get(name), name, cap))

    parts.append(takeaway(
        "линейните модели (Ridge/ElasticNet/Lasso) <b>бият naive ~2.4×</b> по MAE и са най-добри на теста; "
        "дърветата <b>овърфитват</b> (висок train R², близък до 0 или отрицателен test R²). "
        "Сигналът в предлагането е по-слаб от този в товара — затова и R² е по-нисък."))
    parts.append("</section>")
    return "\n".join(parts)


def render_market_intro() -> str:
    """Първа секция: как работи електроенергийният пазар (представя се на живо)."""
    return """
<section id="market"><h2>Как работи електроенергийният пазар — Илиян Сарандалиев</h2>
<p class="lead">Тази секция ще бъде представена на живо.</p>
</section>"""


def render_pipeline_overview() -> str:
    """Уводна секция за журито: как е устроен целият pipeline (с диаграми)."""
    return """
<section id="pipeline"><h2>Как работи pipeline-ът — Борис Станков</h2>
<p class="lead">Цялото решение е един възпроизводим <b>pipeline</b> от четири етапа:
<code>scrape&nbsp;→&nbsp;clean&nbsp;&amp;&nbsp;transform&nbsp;→&nbsp;features&nbsp;→&nbsp;model</code>.
Водещият принцип е <b>„S3 е единственият източник на истина“</b> — всеки етап чете
входа си от S3 и записва изхода обратно в S3. Затова никой локален файл не е
авторитетен и всеки с достъп до bucket-а може да възпроизведе целия резултат от нулата.</p>

<div class="note">Решението е организирано на <b>слоеве (layers)</b>:
<b>Layer&nbsp;1 — потребление (товар)</b>, <b>Layer&nbsp;2 — предлагане (supply)</b> и
<b>Layer&nbsp;3 — цена</b>. <b>Този отчет е крайният резултат и покрива всички готови
слоеве</b> — <b>Layer&nbsp;1</b> и <b>Layer&nbsp;2</b> (резултатите им следват по-долу).
И двата минават през едни и същи етапи, разделени по слой
(<code>transform</code> / <code>features/layer_*</code> / <code>model/layer_*</code>).
Пълният прогон (<code>run_pipeline.py</code> / <code>run_no_scrape.py</code>) върти
<b>всички слоеве</b> и обновява целия отчет; всеки слой може и поотделно, напр.
<code>python&nbsp;model/run_all.py&nbsp;layer_2</code>. Слоят за
<b>цената (Layer&nbsp;3)</b> предстои.</div>

<div class="pipe">
  <div class="pipe-stage src">
    <h4>Източници</h4>
    <ul class="src-list">
      <li>ENTSO-E (товар, цени)</li>
      <li>Open-Meteo (метео + day-ahead прогноза)</li>
      <li>IBEX (15-мин цени)</li>
      <li>празници / уикенди</li>
    </ul>
  </div>
  <div class="pipe-arrow">→</div>
  <div class="pipe-stage"><span class="n">1</span><h4>Scrape</h4>
    <div class="d">сваля суровите данни от публичните източници</div>
    <code class="artifact">data/raw/</code></div>
  <div class="pipe-arrow">→</div>
  <div class="pipe-stage"><span class="n">2</span><h4>Clean &amp; Transform</h4>
    <div class="d">канонични часови master-таблици в локално BG време (Europe/Sofia)</div>
    <code class="artifact">data/processed/ · master_*.csv</code></div>
  <div class="pipe-arrow">→</div>
  <div class="pipe-stage"><span class="n">3</span><h4>Features</h4>
    <div class="d">честни предиктори: лагове, метео-прогноза, календар — без look-ahead</div>
    <code class="artifact">data/processed/ · features_*.csv</code></div>
  <div class="pipe-arrow">→</div>
  <div class="pipe-stage"><span class="n">4</span><h4>Model</h4>
    <div class="d">walk-forward обучение, 4 модела vs ЕСО/naive + conformal интервали</div>
    <code class="artifact">data/results/ · фигури + този отчет</code></div>
</div>

<div class="s3-band">
  <div class="s3-ico">⛁</div>
  <div class="s3-txt"><b>S3 bucket — единственият източник на истина.</b>
    Всеки етап чете входа си от S3 и качва изхода обратно; етапите се свързват
    през S3, не през локални файлове.</div>
  <div class="s3-folders">
    <code class="artifact">data/raw/</code>
    <code class="artifact">data/processed/</code>
    <code class="artifact">data/results/</code>
  </div>
</div>

<div class="cards" style="margin-top:16px">
  <div class="card"><h4>Възпроизводимо</h4>една команда (<code>run_pipeline.py</code>)
    стартира (run) целия chain; всеки етап има и свой <code>run_all.py</code>, който
    може да се изпълни поотделно.</div>
  <div class="card"><h4>Модулно и устойчиво</h4>всеки скрипт е отделен процес —
    при грешка тя се логва и се прескача, без да сваля целия pipeline.</div>
  <div class="card"><h4>Два backend-а</h4>по подразбиране backend-ът е <b>S3</b>
    (или каквото е зададено в <code>STORAGE_BACKEND</code> в <code>.env</code>);
    <code>--local</code> ползва локално огледало (<code>./local_store/</code>) със същия
    ключов layout.</div>
</div>

<h3 style="margin-top:24px">Как се стартира (run)</h3>
<p><b>S3 е по подразбиране</b> — без флаг всеки етап чете входа си от S3 и записва
изхода обратно, а следващият етап го чете оттам. Backend-ът може да се зададе и в
<code>.env</code> (<code>STORAGE_BACKEND=s3|local</code>); флаговете <code>--local</code> /
<code>--s3</code> го override-ват за конкретното изпълнение.</p>
<table>
  <thead><tr><th>Команда</th><th>Какво прави</th><th>Кога</th></tr></thead>
  <tbody>
    <tr>
      <td><code>python run_pipeline.py</code></td>
      <td>и&nbsp;4-те етапа: scrape&nbsp;→&nbsp;transform&nbsp;→&nbsp;features&nbsp;→&nbsp;model</td>
      <td>пълен run от нулата (вкл. сваляне на данните)</td>
    </tr>
    <tr>
      <td><code>python run_no_scrape.py</code></td>
      <td>3&nbsp;етапа: transform&nbsp;→&nbsp;features&nbsp;→&nbsp;model</td>
      <td>суровите данни вече са в S3 — само преизграждаш master-и&nbsp;→&nbsp;features&nbsp;→&nbsp;модели&nbsp;+&nbsp;отчет</td>
    </tr>
    <tr>
      <td><code>python model/run_all.py</code> <span class="badge">layer_1</span></td>
      <td>само етап „model“ за Layer&nbsp;1 (3&nbsp;модела&nbsp;×&nbsp;хоризонт + този отчет)</td>
      <td>преобучаване само на моделите за потреблението</td>
    </tr>
    <tr>
      <td><code>python model/run_all.py layer_2</code></td>
      <td>Layer&nbsp;2 (предлагане) — обучава supply моделите</td>
      <td>извън <code>run_pipeline.py</code>; фигурите му влизат в този отчет (секция Layer&nbsp;2)</td>
    </tr>
  </tbody>
</table>
<div class="cards" style="margin-top:14px">
  <div class="card"><h4>--local / --s3</h4><code>--local</code> ползва локален backend
    (<code>./local_store/</code>) за <b>четене и запис</b> — със същия ключов layout като
    S3, затова локалният run <b>се верижи</b> (всеки етап чете каквото предходният току-що е
    записал локално). <code>--s3</code> форсира S3 за конкретния run.</div>
  <div class="card"><h4>.env</h4>стоящ default за backend-а и креденшъли
    (<code>STORAGE_BACKEND</code>, <code>S3_BUCKET</code>, AWS ключове) — зарежда се
    автоматично; реалните env-променливи го надделяват.</div>
  <div class="card"><h4>--no-open</h4>не отваря автоматично HTML отчета накрая
    (за headless/CI). По подразбиране отчетът се отваря в браузъра.</div>
  <div class="card"><h4>--from STAGE</h4>само за <code>run_pipeline.py</code> — започва от
    даден етап, напр. <code>--from features</code> прескача scrape&nbsp;+&nbsp;transform.</div>
</div>

<p style="margin-top:18px"><b>Кой backend се ползва?</b> Решава се по приоритет —
първото налично печели:</p>
<ol class="prec">
  <li><b>Флаг</b> на командата: <code>--local</code> или <code>--s3</code></li>
  <li><b><code>STORAGE_BACKEND</code></b> от <code>.env</code> или env-променлива (<code>s3</code> | <code>local</code>)</li>
  <li><b>По подразбиране</b>: <code>s3</code></li>
</ol>
<div class="note"><code>.env</code> се зарежда автоматично и е <b>по желание</b> — реалните
env-променливи имат предимство пред него. Без никаква конфигурация pipeline-ът работи на S3.</div>
</section>"""


def main() -> int:
    # Persist the built page by default; backend (s3/local) chosen in upload_s3.
    # --no-upload regenerates the local index.html only (figures still read from
    # the active backend) — for previewing report edits without touching S3.
    do_upload = "--no-upload" not in sys.argv

    by_horizon, source = collect()
    built = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    parts = [_HEAD.format(built=built, source=html.escape(source))]

    # Навигация.
    titles = dict(SECTIONS)
    nav_links = ['<a href="#market">Пазар</a>',
                 '<a href="#pipeline">Как работи</a>']
    if "1d" in by_horizon:
        nav_links.append('<a href="#layer1">Layer 1 · 24ч</a>')
    if "supply" in by_horizon:
        nav_links.append('<a href="#layer2">Layer 2 · supply</a>')
    parts.append('<nav>' + " ".join(nav_links) + '</nav>')
    parts.append("<main>")

    # Първа секция: как работи електроенергийният пазар (представя се на живо).
    parts.append(render_market_intro())
    # Уводна секция за журито — как работи целият pipeline (с диаграми).
    parts.append(render_pipeline_overview())

    any_figs = False
    # Layer 1 хоризонти първо (в реда на SECTIONS), после Layer 2 (supply), после
    # всякакви други резултатни папки по азбучен ред.
    known = {h for h, _ in SECTIONS} | {"supply"}
    ordered = [h for h, _ in SECTIONS] + ["supply"] + sorted(set(by_horizon) - known)
    for hz in ordered:
        figs = by_horizon.get(hz)
        if not figs:
            continue
        any_figs = True
        if hz == "1d":
            block, _ = render_1d(figs)
            parts.append(block)
        elif hz == "supply":
            parts.append(render_layer2(figs))
        else:
            parts.append(render_plain(titles.get(hz, hz), figs, hz))

    if not any_figs:
        parts.append('<section><p class="empty">Няма резултатни PNG още — '
                     'стартирай (run) model builder-ите първо (S3 по подразбиране).</p></section>')

    parts.append(_FOOT)
    page = "\n".join(parts)

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = RESULTS_DIR / OUT_NAME
    out_path.write_text(page, encoding="utf-8")
    print(f"\n✅ записах {out_path} ({len(page) / 1024:.0f} KB)")

    if do_upload:
        from upload_s3 import upload  # noqa: PLC0415
        upload(out_path, prefix="data/results")
    return 0


_HEAD = """<!doctype html>
<html lang="bg">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Прогноза на електроенергийния пазар — резултати</title>
<style>
  :root {{ color-scheme: light dark; }}
  * {{ box-sizing: border-box; }}
  body {{ font-family: -apple-system, Segoe UI, Roboto, sans-serif; margin: 0;
         background: #0f172a; color: #e2e8f0; line-height: 1.55; }}
  header {{ padding: 24px 32px; background: #1e293b; border-bottom: 1px solid #334155; }}
  header h1 {{ margin: 0 0 4px; font-size: 22px; }}
  header p {{ margin: 0; color: #94a3b8; font-size: 13px; }}
  nav {{ padding: 12px 32px; background: #162033; font-size: 13px; position: sticky; top: 0; z-index: 9;
        border-bottom: 1px solid #243a5e; }}
  nav a {{ color: #60a5fa; margin-right: 16px; text-decoration: none; }}
  main {{ padding: 24px 32px 64px; max-width: 1080px; margin: 0 auto; }}
  section {{ margin-bottom: 40px; }}
  section h2 {{ font-size: 20px; border-left: 4px solid #2563eb; padding-left: 12px; margin: 0 0 14px; }}
  .lead {{ color: #cbd5e1; font-size: 16px; background: #14233f; border: 1px solid #25395d;
          border-radius: 10px; padding: 14px 18px; }}
  .step {{ margin: 22px 0; }}
  .step h3 {{ font-size: 17px; margin: 0 0 8px; color: #e2e8f0;
             border-bottom: 1px solid #243a5e; padding-bottom: 6px; }}
  .step h4 {{ font-size: 13px; margin: 0 0 6px; color: #38bdf8; text-transform: uppercase; letter-spacing: .04em; }}
  ul, ol {{ margin: .3em 0; padding-left: 1.25em; }}
  li {{ margin: .25em 0; }}
  b {{ color: #fff; }}
  code {{ background: #0a1426; color: #aee0ff; padding: 1px 6px; border-radius: 5px; font-size: .9em;
         border: 1px solid #1c2c47; }}
  .badge {{ font-size: 12px; background: #2563eb; color: #fff; border-radius: 999px; padding: 2px 10px;
           vertical-align: middle; margin-left: 6px; }}
  .pick {{ color: #34d399; }}
  table {{ border-collapse: collapse; width: 100%; font-size: 14px; margin: .4em 0;
          background: #14233f; border-radius: 8px; overflow: hidden; }}
  th {{ background: #16243f; text-align: left; padding: 9px 12px; color: #cfe0ff; }}
  td {{ padding: 8px 12px; border-top: 1px solid #243a5e; vertical-align: top; }}
  .cards {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 14px; }}
  .card {{ background: #14233f; border: 1px solid #25395d; border-radius: 10px; padding: 14px 16px; }}
  .card ul {{ font-size: 14px; }}
  .note {{ color: #94a3b8; font-size: 14px; margin-top: 12px; border-left: 3px solid #2b3e5e; padding-left: 12px; }}
  .takeaway {{ margin-top: 14px; background: #10231a; border: 1px solid #1d5e3b; border-radius: 10px;
              padding: 11px 16px; font-size: 15px; }}
  .missing {{ background: #2a1416; border: 1px solid #5e2a2a; border-radius: 10px; padding: 16px; color: #ffb4b4; }}
  .bigstat {{ display: flex; gap: 22px; flex-wrap: wrap; margin: .4em 0; }}
  .stat {{ background: #14233f; border: 1px solid #25395d; border-radius: 12px; padding: 18px 28px; text-align: center; }}
  .num {{ font-size: 40px; font-weight: 800; color: #34d399; }}
  .lbl {{ color: #94a3b8; font-size: 13px; margin-top: 4px; }}
  ol.keys li {{ margin: .4em 0; }}
  figure {{ margin: 16px 0 20px; background: #fff; border-radius: 8px; overflow: hidden;
           box-shadow: 0 1px 4px rgba(0,0,0,.4); }}
  figcaption {{ font-size: 13px; color: #334155; padding: 8px 12px; background: #f1f5f9;
               border-bottom: 1px solid #e2e8f0; }}
  img {{ display: block; width: 100%; height: auto; }}
  .empty {{ color: #94a3b8; }}
  /* --- pipeline overview diagrams --- */
  .pipe {{ display: flex; align-items: stretch; gap: 6px; flex-wrap: wrap; margin: 20px 0 12px; }}
  .pipe-stage {{ position: relative; flex: 1 1 150px; min-width: 148px; background: #13243f;
                border: 1px solid #2b4a7a; border-radius: 12px; padding: 28px 12px 12px; }}
  .pipe-stage.src {{ background: #1a2436; border-color: #3a4d6b; padding-top: 12px; }}
  .pipe-stage .n {{ position: absolute; top: -13px; left: 12px; width: 26px; height: 26px;
                   background: #2563eb; color: #fff; border-radius: 50%; display: flex;
                   align-items: center; justify-content: center; font-weight: 700; font-size: 14px; }}
  .pipe-stage h4 {{ margin: 0 0 5px; font-size: 15px; color: #fff; }}
  .pipe-stage .d {{ font-size: 12px; color: #9fb3d1; }}
  .pipe-stage .src-list {{ font-size: 12px; color: #cbd5e1; margin: 4px 0 0; padding-left: 15px; }}
  .pipe-stage .src-list li {{ margin: 1px 0; }}
  .artifact {{ display: inline-block; margin-top: 8px; background: #0a1426; border: 1px solid #1c3358;
              color: #aee0ff; border-radius: 6px; padding: 2px 7px; font-size: 11px;
              font-family: ui-monospace, monospace; }}
  .pipe-arrow {{ display: flex; align-items: center; justify-content: center; color: #3b82f6;
                font-size: 24px; font-weight: 800; flex: 0 0 16px; }}
  .s3-band {{ display: flex; align-items: center; gap: 14px; flex-wrap: wrap;
             background: linear-gradient(90deg, #0b2233, #0e1c33); border: 1px solid #1d6e8a;
             border-radius: 12px; padding: 14px 18px; margin: 10px 0 4px; }}
  .s3-band .s3-ico {{ font-size: 30px; line-height: 1; }}
  .s3-band .s3-txt {{ font-size: 14px; color: #cbd5e1; flex: 1 1 280px; }}
  .s3-folders {{ display: flex; gap: 8px; flex-wrap: wrap; margin-left: auto; }}
  .prec {{ counter-reset: p; list-style: none; padding-left: 0; margin: 8px 0; }}
  .prec li {{ position: relative; padding: 8px 12px 8px 40px; margin: 6px 0; background: #14233f;
             border: 1px solid #25395d; border-radius: 8px; font-size: 14px; }}
  .prec li::before {{ counter-increment: p; content: counter(p); position: absolute; left: 10px;
                     top: 50%; transform: translateY(-50%); width: 20px; height: 20px;
                     background: #2563eb; color: #fff; border-radius: 50%; font-size: 12px;
                     font-weight: 700; display: flex; align-items: center; justify-content: center; }}
  .timeline {{ display: flex; gap: 12px; flex-wrap: wrap; margin: 10px 0; }}
  .timeline .card {{ flex: 1 1 260px; }}
  .card.gate {{ border-color: #1d5e3b; background: #10231a; }}
  .card.deliver {{ border-color: #2b4a7a; background: #11203a; }}
</style>
</head>
<body>
<header>
  <h1>Прогноза на електроенергийния пазар — резултати от моделите</h1>
  <p>Layer 1 · потребление (товар) &nbsp;·&nbsp; Layer 2 · предлагане (supply) &nbsp;—&nbsp;
     изградено {built} · фигури от {source} · самостоятелна страница (изображенията са вградени)</p>
</header>"""

_FOOT = """</main>
</body>
</html>"""


if __name__ == "__main__":
    sys.exit(main())
