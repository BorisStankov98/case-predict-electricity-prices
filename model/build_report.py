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
    python build_report.py            # построй + качи страницата в data/results/ (S3 по подразбиране)
    python build_report.py --local    # построй само локално index.html (без качване)
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
FIG_ORDER = ["pipeline_metrics", "pipeline_significant", "pipeline_selection",
             "pipeline_diagnostics", "pipeline_intervals", "pipeline_learning_curve",
             "pipeline_final", "pipeline_15min_ramp", "pipeline_corr"]


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
    parts.append('<section id="layer1"><h2>Layer 1 — Прогноза на потреблението (товара) · 24ч</h2>')
    parts.append(
        '<p class="lead">Пазар: <b>България</b>. Цел: честна прогноза на товара (MW) за '
        '<b>ден напред</b>, която <b>бие официалната прогноза на ЕСО</b>. '
        'Бенчмарки: ЕСО (day-ahead) и naive (предния ден). '
        'Водещ принцип — <b>без look-ahead</b>: всеки feature ползва само информация, '
        'налична на момента на прогнозата.</p>')

    parts.append(step("0 · Цел и принципи", ul([
        "<b>Цел:</b> прогноза на товара (MW) за <b>ден напред (24ч)</b>, плюс 1 седмица и 15 мин.",
        "<b>Бенчмарки:</b> официалната day-ahead прогноза на <b>ЕСО</b> и <b>naive</b> (предния ден).",
        "<b>Без look-ahead:</b> всеки feature ползва само налична към момента информация — "
        "честността е лайтмотивът на цялата работа.",
    ])))

    parts.append(step("1 · Данни (източници)", table(["данни", "роля"], [
        ["<code>load_actual</code> (ENTSO-E)", "<b>таргет</b> — реалният товар"],
        ["<code>load_forecast_day_ahead</code> (ЕСО)", "<b>бенчмарк</b> (НЕ е feature)"],
        ["<code>bulgaria_1day_ahead_forecast</code> (open-meteo)", "<b>реална day-ahead метео-ПРОГНОЗА</b> за час T"],
        ["<code>days_off_bg</code>", "празници / уикенди"],
    ]) + note("Канонизирани в обща часова решетка, <b>локално българско време</b> "
              "(Europe/Sofia, tz-aware), от единен възпроизводим builder.")))

    parts.append(step("2 · Стационарност (ADF)",
        ul([
            "<b>Изследване:</b> ADF тест на товара — ако нивото не е стационарно, би трябвало да "
            "диференцираме (Δ24 = load − lag24).",
            "<b>Резултат:</b> товарът <b>Е стационарен по ADF</b> (p≈0.008). Видимата "
            "„нестационарност“ е <b>сезонност</b> (дневен/седмичен ритъм), <b>не unit-root</b>.",
        ]) + takeaway("НЕ диференцираме. Сезонността се поема от детерминистични сезонни "
                      "feature-и (календар + лагове). (Δ24 тестван: без печалба.)")))

    parts.append(step("3 · Честна методология (no look-ahead) — гръбнакът", ul([
        "<b>Метео:</b> реалното време за T е look-ahead → ползваме <b>day-ahead ПРОГНОЗА за T</b> "
        "(или <code>lag24</code> proxy, ако липсва).",
        "<b>Товарни лагове:</b> само <b>≥24ч</b> (никога стойност от деня на доставка).",
        "<b>Календар:</b> детерминистичен → винаги наличен.",
        "<b>Оценка:</b> rolling <b>walk-forward</b> — тестов блок след train, никога върху обучаван период.",
    ])))

    parts.append(step("4 · Feature engineering <span class=\"badge\">45 → 36 след подбор</span>",
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

    parts.append(step("5 · ГОЛЯМАТА НАХОДКА: реална метео-прогноза vs lag24 proxy",
        '<div class="bigstat">'
        '<div class="stat"><div class="num">−24%</div><div class="lbl">XGBoost MAE<br>151.5 → 115.8</div></div>'
        '<div class="stat"><div class="num">−16%</div><div class="lbl">Ridge MAE<br>148.9 → 125.1</div></div>'
        '</div>' +
        takeaway("реалната прогноза носи <b>нелинеен метео-сигнал</b> → <b>XGBoost става водещ</b> при "
                 "24ч (при proxy метеото беше шум и линейните печелеха). Това вдига цялото L1.")))

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
        ]) +
        takeaway("превъзходството над ЕСО <b>не е случайно</b> — статистически значимо е.")))

    parts.append(step("11 · Подбор на features — какво НЕ работи",
        f("pipeline_significant.png", "Значими / важни features за всеки модел") +
        ul([
            "Махане по <b>ниска корелация</b> — ❌ грешно (пропуска нелинейности).",
            "Махане по <b>висока мултиколинеарност</b> — ⚠️ ВРЕДИ силно (Ridge +19 MAE!): "
            "„колинеарно ≠ редундантно“.",
            "Комбиниран критерий (незначим И |corr|&lt;0.2): махна 9 feature-а (45→36) <b>без загуба</b>.",
        ]) +
        f("pipeline_corr_ElasticNet.png", "Корелации feature→товар и feature×feature (ElasticNet)") +
        takeaway("остави моделите да избират (Lasso/ElasticNet нулират ненужните); махай ръчно само "
                 "точни дубликати; валидирай с OOS, не с univariate статистики.")))

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

    parts.append(step("15 · Learning curve (избрания модел)",
        f("pipeline_learning_curve_ElasticNet.png", "train vs test MAE спрямо обема обучение") +
        takeaway("нужни са поне ~3–4 месеца данни; плато около 1 година — повече почти не помага.")))

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

    parts.append(step("Ключови изводи",
        '<ol class="keys">'
        '<li><b>Честната методология работи:</b> без look-ahead, и пак бием ЕСО с ~30% (значимо).</li>'
        '<li><b>Реалната метео-прогноза е решаващата стъпка</b> (−24% спрямо proxy); прави XGBoost конкурентен.</li>'
        '<li><b>Линеен модел (ElasticNet) е най-балансираният</b> избор; XGBoost овърфитва.</li>'
        '<li><b>Подборът на features: остави моделите да избират;</b> ръчното рязане по корелация вреди.</li>'
        '<li><b>Грешките не са бял шум</b> (присъщо на 24ч), хетероскедастични и не-нормални → <b>Mondrian conformal</b>.</li>'
        '<li><b>Товарът е сезонно-, не unit-root-нестационарен</b> → не се диференцира.</li>'
        '<li><b>Три хоризонта покрити:</b> 24ч (силен), 1 седм (+19% над naive), 15 мин (дезагрегация).</li>'
        '</ol>'))

    # Останалите 1d фигури, които не са показани в разказа — приложение.
    extras = [n for n in sorted(figs, key=fig_sort_key) if n not in used]
    if extras:
        body = "".join(figure(figs[n], n) for n in extras)
        parts.append(step("Приложение · Допълнителни 1d фигури", body))

    parts.append("</section>")
    return "\n".join(parts), used


# ── обикновена секция (15min, 1week): подредени фигури, вертикално ───────────
def render_plain(title: str, figs: dict[str, str]) -> str:
    parts = [f'<section><h2>{html.escape(title)}</h2>']
    for name in sorted(figs, key=fig_sort_key):
        parts.append(figure(figs[name], name))
    parts.append("</section>")
    return "\n".join(parts)


def render_pipeline_overview() -> str:
    """Уводна секция за журито: как е устроен целият pipeline (с диаграми)."""
    return """
<section id="pipeline"><h2>Как работи pipeline-ът</h2>
<p class="lead">Цялото решение е един възпроизводим <b>pipeline</b> от четири етапа:
<code>scrape&nbsp;→&nbsp;clean&nbsp;&amp;&nbsp;transform&nbsp;→&nbsp;features&nbsp;→&nbsp;model</code>.
Водещият принцип е <b>„S3 е единственият източник на истина“</b> — всеки етап чете
входа си от S3 и записва изхода обратно в S3. Затова никой локален файл не е
авторитетен и всеки с достъп до bucket-а може да възпроизведе целия резултат от нулата.</p>

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
      <td><code>python model/run_all.py</code></td>
      <td>само етап „model“ (4&nbsp;модела + този отчет)</td>
      <td>преобучаване само на моделите</td>
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

<h3 style="margin-top:24px">Честна методология — без look-ahead</h3>
<p>Сърцето на Layer 1: всеки feature ползва само информация, налична
<b>на момента на прогнозата</b> (ден предварително). Затова разделяме ясно
какво <b>знаем на гейта</b> от това, което <b>прогнозираме</b>.</p>
<div class="timeline">
  <div class="card gate"><h4>🔒 Ден D−1 · гейт — какво знаем</h4>
    <ul>
      <li>товарни лагове само <b>≥24ч</b> (<code>lag24/48/168…</code>)</li>
      <li>реална <b>day-ahead метео-ПРОГНОЗА</b> за всеки час на ден D</li>
      <li>детерминистичен календар (празници/уикенди)</li>
    </ul></div>
  <div class="pipe-arrow">→</div>
  <div class="card deliver"><h4>🎯 Ден D · прогнозираме 24ч</h4>
    <ul>
      <li>прогноза на товара (MW) за всеки час</li>
      <li>оценка: rolling <b>walk-forward</b> (тестов блок след train)</li>
      <li>сравнение с ЕСО и naive + 90% conformal интервали</li>
    </ul></div>
</div>
<div class="note">Подробните данни, методи и резултати по хоризонти следват по-долу.</div>
</section>"""


def main() -> int:
    do_upload = True  # always persist; backend (s3/local) chosen in upload_s3

    by_horizon, source = collect()
    built = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    parts = [_HEAD.format(built=built, source=html.escape(source))]

    # Навигация.
    titles = dict(SECTIONS)
    nav_links = ['<a href="#pipeline">Как работи</a>']
    if "1d" in by_horizon:
        nav_links.append('<a href="#layer1">Layer 1 · 24ч</a>')
    parts.append('<nav>' + " ".join(nav_links) + '</nav>')
    parts.append("<main>")

    # Уводна секция за журито — как работи целият pipeline (с диаграми).
    parts.append(render_pipeline_overview())

    any_figs = False
    ordered = [h for h, _ in SECTIONS] + sorted(set(by_horizon) - {h for h, _ in SECTIONS})
    for hz in ordered:
        figs = by_horizon.get(hz)
        if not figs:
            continue
        any_figs = True
        if hz == "1d":
            block, _ = render_1d(figs)
            parts.append(block)
        else:
            parts.append(render_plain(titles.get(hz, hz), figs))

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
<title>Прогноза на товара — резултати</title>
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
  <h1>Прогноза на електрическия товар — резултати от моделите</h1>
  <p>Изградено {built} · фигури от {source} · самостоятелна страница (изображенията са вградени)</p>
</header>"""

_FOOT = """</main>
</body>
</html>"""


if __name__ == "__main__":
    sys.exit(main())
