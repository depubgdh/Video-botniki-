const tg = window.Telegram && window.Telegram.WebApp;
if (tg) {
    tg.ready();
    tg.expand();
}
const initData = tg ? tg.initData : "";

const $ = (id) => document.getElementById(id);
const BEAT_MS = 5000;

let sessionId = null;
let player = null;
let playerState = -1;
let beatTimer = null;
let busy = false;
let refLink = "";

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
    for (const s of ["home", "watch", "result"]) {
        $(s + "-screen").style.display = s === name ? "block" : "none";
    }
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
    if (document.hidden) el.textContent = "Paused (App closed/minimized)";
    else if (playerState === 1) el.textContent = "Verified seconds counting...";
    else if (playerState === 3) el.textContent = "Buffering...";
    else el.textContent = "Paused";
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

// ---------- Asosiy oqim ----------

async function loadMe() {
    try {
        const me = await api("/api/me");
        $("user-balance").textContent = money(me.balance);
        $("friends-text").textContent = `${me.friends_active} active / ${me.friends_total} total`;
        refLink = me.ref_link;
    } catch (e) {
        toast(e.message);
    }
}

async function startWatching() {
    if (busy || sessionId) return;
    busy = true;
    try {
        const res = await api("/api/start");
        sessionId = res.session_id;
        show("watch");
        $("video-title").textContent = res.video.title || "";
        $("live-reward").textContent = "+0.0000$";
        $("status-text").textContent = "Loading...";
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
        $("user-balance").textContent = money(r.balance);
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
        show("home");
        return;
    }
    try {
        const r = await api("/api/stop", { session_id: sid });
        $("user-balance").textContent = money(r.balance);
        $("res-time").textContent = `${r.watched}s`;
        $("res-earned").textContent = "+" + money(r.earned, 4);
        show(silent ? "home" : "result");
    } catch (e) {
        toast(e.message);
        show("home");
    }
}

function goHome() {
    show("home");
    loadMe();
}

function inviteFriends() {
    if (!refLink) return toast("Havola hali tayyor emas");
    const text = "Watch videos and earn on VidiPay!";
    if (tg && tg.openTelegramLink) {
        tg.openTelegramLink(
            `https://t.me/share/url?url=${encodeURIComponent(refLink)}&text=${encodeURIComponent(text)}`);
    } else if (navigator.clipboard) {
        navigator.clipboard.writeText(refLink).then(() => toast("Havola nusxalandi"));
    }
}

// Foydalanuvchi ilovadan chiqib ketsa — hisoblash to'xtaydi (server ham playing=false ni oladi)
document.addEventListener("visibilitychange", () => {
    if (sessionId) updateStatus();
});

loadMe();
