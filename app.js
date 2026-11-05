const tg = window.Telegram && window.Telegram.WebApp;
if (tg) {
    tg.ready();
    tg.expand();
    try {
        tg.setHeaderColor("#0f0b1e");
        tg.setBackgroundColor("#0f0b1e");
    } catch (_) {}
}
const initData = tg ? tg.initData : "";

const $ = (id) => document.getElementById(id);
const BEAT_MS = 5000;

let sessionId = null;
let player = null;
let playerState = -1;
let beatTimer = null;
let busy = false;

// ---------- Yordamchi funksiyalar ----------

async function api(path, body = {}) {
    const res = await fetch(path, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ initData, ...body }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
        const err = new Error(data.error || "Server xatosi");
        err.status = res.status;
        throw err;
    }
    return data;
}

function show(name) {
    for (const s of ["home", "profile", "watch", "result"]) {
        $(s + "-screen").classList.toggle("active", s === name);
    }
    $("nav").classList.toggle("on", name === "home" || name === "profile");
    $("nav-home").classList.toggle("active", name === "home");
    $("nav-profile").classList.toggle("active", name === "profile");
    window.scrollTo(0, 0);
}

let toastTimer = null;
function toast(msg) {
    const t = $("toast");
    t.textContent = msg;
    t.style.display = "block";
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => (t.style.display = "none"), 4000);
}

function money(n, digits = 2) {
    return n.toFixed(digits) + "$";
}

function moneyAuto(n) {
    return money(n, n > 0 && n < 0.01 ? 4 : 2);
}

function dur(sec) {
    sec = Math.floor(sec);
    if (sec < 60) return sec + " s";
    const m = Math.floor(sec / 60);
    if (m < 60) return m + " daq";
    const h = Math.floor(m / 60);
    return h + " soat " + (m % 60) + " daq";
}

// ---------- YouTube pleyer ----------

const ytReady = new Promise((resolve) => {
    window.onYouTubeIframeAPIReady = resolve;
    const s = document.createElement("script");
    s.src = "https://www.youtube.com/iframe_api";
    document.head.appendChild(s);
});

function isPlaying() {
    return playerState === 1 && !document.hidden;
}

function updateStatus() {
    const el = $("status-text");
    if (document.hidden) el.textContent = "Pauza (ilova yopilgan)";
    else if (playerState === 1) el.textContent = "Ko'rilayotgan soniyalar hisoblanmoqda...";
    else if (playerState === 3) el.textContent = "Yuklanmoqda...";
    else el.textContent = "Pauza";
}

async function createPlayer(videoId) {
    await Promise.race([
        ytReady,
        new Promise((_, reject) =>
            setTimeout(() => reject(new Error("YouTube pleyeri yuklanmadi")), 10000)),
    ]);
    $("video-container").innerHTML = '<div id="yt-player"></div>';
    playerState = -1;
    player = new YT.Player("yt-player", {
        videoId,
        width: "100%",
        height: "100%",
        playerVars: { autoplay: 1, playsinline: 1, rel: 0, modestbranding: 1 },
        events: {
            onStateChange: (e) => {
                playerState = e.data;
                updateStatus();
                if (e.data === 0) stopWatching(); // video tugadi
            },
            onError: () => {
                toast("Video ochilmadi");
                stopWatching();
            },
        },
    });
}

// ---------- Profil va bosh sahifa ----------

function renderHistory(items) {
    const box = $("history-list");
    box.textContent = "";
    if (!items.length) {
        const d = document.createElement("div");
        d.className = "empty";
        d.textContent = "Hali video ko'rilmagan";
        box.appendChild(d);
        return;
    }
    for (const h of items) {
        const row = document.createElement("div");
        row.className = "hist";
        const t = document.createElement("div");
        t.className = "t";
        const title = document.createElement("b");
        title.textContent = h.title;
        const sub = document.createElement("span");
        const when = new Date(h.at * 1000).toLocaleString([], {
            day: "numeric", month: "short", hour: "2-digit", minute: "2-digit",
        });
        sub.textContent = `${dur(h.seconds)} · ${when}`;
        t.append(title, sub);
        const e = document.createElement("div");
        e.className = "e";
        e.textContent = "+" + moneyAuto(h.earned);
        row.append(t, e);
        box.appendChild(row);
    }
}

async function loadMe() {
    try {
        const me = await api("/api/me");
        const first = (me.fullname || "").split(" ")[0] || "do'st";

        if (me.app_name) {
            document.title = me.app_name;
            const parts = me.app_name.split(" ");
            const last = parts.pop();
            $("app-name").innerHTML = "";
            $("app-name").append(parts.join(" ") + (parts.length ? " " : ""));
            const sp = document.createElement("span");
            sp.textContent = last;
            $("app-name").appendChild(sp);
        }
        $("greeting").textContent = `Salom, ${first}!`;
        $("user-balance").textContent = me.balance.toFixed(2);
        $("today-earned").textContent = `+${moneyAuto(me.today_earned)} bugun`;
        $("st-today").textContent = dur(me.today_seconds);
        $("st-total").textContent = dur(me.total_seconds);
        $("st-income").textContent = money(me.total_earned);

        $("avatar").textContent = (first[0] || "?").toUpperCase();
        $("pf-name").textContent = me.fullname || first;
        $("pf-username").textContent = me.username ? "@" + me.username : "";
        $("pf-id").textContent = "ID " + me.user_id;
        $("pf-balance").textContent = money(me.balance);
        $("pf-total-time").textContent = dur(me.total_seconds);
        $("pf-total-earned").textContent = money(me.total_earned);
        renderHistory(me.history || []);
    } catch (e) {
        toast(e.message);
    }
}

function goHome() {
    show("home");
    loadMe();
}

function openProfile() {
    show("profile");
    loadMe();
}

// ---------- Ko'rish oqimi ----------

async function startWatching() {
    if (busy || sessionId) return;
    busy = true;
    try {
        const res = await api("/api/start");
        sessionId = res.session_id;
        show("watch");
        $("video-title").textContent = res.video.title || "";
        $("live-reward").textContent = "+0.0000$";
        $("status-text").textContent = "Yuklanmoqda...";
        await createPlayer(res.video.youtube_id);
        beatTimer = setInterval(beat, BEAT_MS);
    } catch (e) {
        toast(e.message);
        await stopWatching(true);
    } finally {
        busy = false;
    }
}

async function beat() {
    if (!sessionId) return;
    try {
        const r = await api("/api/beat", { session_id: sessionId, playing: isPlaying() });
        $("live-reward").textContent = "+" + money(r.earned, 4);
        $("user-balance").textContent = r.balance.toFixed(2);
    } catch (e) {
        toast(e.message);
        if (e.status === 409) stopWatching(true);
    }
}

async function stopWatching(silent = false) {
    const sid = sessionId;
    sessionId = null;
    clearInterval(beatTimer);
    beatTimer = null;
    try { if (player) player.destroy(); } catch (_) {}
    player = null;
    playerState = -1;
    $("video-container").innerHTML = "";

    if (!sid) {
        goHome();
        return;
    }
    try {
        const r = await api("/api/stop", { session_id: sid });
        $("res-time").textContent = dur(r.watched);
        $("res-earned").textContent = "+" + moneyAuto(r.earned);
        if (silent) goHome();
        else show("result");
    } catch (e) {
        toast(e.message);
        goHome();
    }
}

// Ilova yopilsa yoki yashirilsa — hisoblash to'xtaydi (server ham playing=false ni oladi)
document.addEventListener("visibilitychange", () => {
    if (sessionId) updateStatus();
});

loadMe();
