/**
 * Source Workspace: YouTube IFrame API + Set Start/End + S/E/Space shortcuts.
 */
let ytPlayer = null;
let ytReady = false;

function roundTime(value) {
  return Math.round(Number(value) * 1000) / 1000;
}

function currentTime() {
  if (!ytReady || !ytPlayer || typeof ytPlayer.getCurrentTime !== "function") {
    return 0;
  }
  return roundTime(ytPlayer.getCurrentTime());
}

function updateDurationDisplay() {
  const startInput = document.querySelector("[data-start-input]");
  const endInput = document.querySelector("[data-end-input]");
  const display = document.querySelector("[data-duration-display]");
  if (!(startInput instanceof HTMLInputElement) || !(endInput instanceof HTMLInputElement)) {
    return;
  }
  const start = Number(startInput.value);
  const end = Number(endInput.value);
  if (display instanceof HTMLElement && Number.isFinite(start) && Number.isFinite(end) && end > start) {
    display.textContent = `Duration ${roundTime(end - start)}s`;
  } else if (display instanceof HTMLElement) {
    display.textContent = "";
  }
}

function setStartFromPlayer() {
  const startInput = document.querySelector("[data-start-input]");
  if (!(startInput instanceof HTMLInputElement)) return;
  startInput.value = String(currentTime());
  updateDurationDisplay();
}

function setEndFromPlayer() {
  const endInput = document.querySelector("[data-end-input]");
  if (!(endInput instanceof HTMLInputElement)) return;
  endInput.value = String(currentTime());
  updateDurationDisplay();
}

function togglePlay() {
  if (!ytReady || !ytPlayer) return;
  const state = ytPlayer.getPlayerState?.();
  // 1 = playing
  if (state === 1) {
    ytPlayer.pauseVideo?.();
  } else {
    ytPlayer.playVideo?.();
  }
}

function isTypingTarget(target) {
  return (
    target instanceof HTMLElement &&
    (target.tagName === "INPUT" ||
      target.tagName === "TEXTAREA" ||
      target.tagName === "SELECT" ||
      target.isContentEditable)
  );
}

function setupShortcuts() {
  document.addEventListener("keydown", (event) => {
    if (isTypingTarget(event.target)) return;
    const root = document.querySelector("[data-source-workspace]");
    if (!root) return;

    if (event.key === "s" || event.key === "S") {
      event.preventDefault();
      setStartFromPlayer();
      return;
    }
    if (event.key === "e" || event.key === "E") {
      event.preventDefault();
      setEndFromPlayer();
      return;
    }
    if (event.code === "Space" || event.key === " ") {
      event.preventDefault();
      togglePlay();
    }
  });
}

function setupButtons() {
  document.querySelector("[data-set-start]")?.addEventListener("click", (event) => {
    event.preventDefault();
    setStartFromPlayer();
  });
  document.querySelector("[data-set-end]")?.addEventListener("click", (event) => {
    event.preventDefault();
    setEndFromPlayer();
  });
  document.querySelector("[data-source-play]")?.addEventListener("click", (event) => {
    event.preventDefault();
    togglePlay();
  });
  document.querySelector("[data-start-input]")?.addEventListener("input", updateDurationDisplay);
  document.querySelector("[data-end-input]")?.addEventListener("input", updateDurationDisplay);
  updateDurationDisplay();
  document.querySelector("[data-created-segment]")?.scrollIntoView({ block: "nearest" });
  setupQuickCreate();
}

function seekSource(seconds) {
  if (!ytReady || !ytPlayer || typeof ytPlayer.seekTo !== "function") return;
  const seek = Number(seconds);
  if (!Number.isFinite(seek) || seek < 0) return;
  ytPlayer.seekTo(seek, true);
}

function setupQuickCreate() {
  const form = document.querySelector("[data-create-segment-form]");
  if (!(form instanceof HTMLFormElement) || form.dataset.quickBound === "1") return;
  form.dataset.quickBound = "1";
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const button = form.querySelector("[type='submit']");
    const status = document.querySelector("[data-create-status]");
    const idle = button instanceof HTMLButtonElement ? button.dataset.labelIdle || button.textContent : "";
    const busy = button instanceof HTMLButtonElement ? button.dataset.labelBusy || idle : "";
    if (button instanceof HTMLButtonElement) {
      button.disabled = true;
      button.textContent = busy;
    }
    try {
      const response = await fetch(form.getAttribute("action") || window.location.href, {
        method: "POST",
        body: new FormData(form),
        headers: { "X-Segment-Create": "1", Accept: "application/json" },
      });
      const payload = await response.json();
      if (!response.ok || !payload.ok) {
        throw new Error(payload.message || "Could not create the segment.");
      }
      const list = document.querySelector("[data-source-segment-list]");
      if (list && typeof payload.list_html === "string") {
        list.innerHTML = payload.list_html;
        window.yalBindSourceBulk?.();
      }
      const startInput = form.querySelector("[data-start-input]");
      const endInput = form.querySelector("[data-end-input]");
      if (startInput instanceof HTMLInputElement) startInput.value = String(payload.next_start);
      if (endInput instanceof HTMLInputElement) endInput.value = String(payload.next_end);
      const transcript = form.querySelector("[name='transcript']");
      if (transcript instanceof HTMLTextAreaElement) transcript.value = "";
      updateDurationDisplay();
      if (payload.seek_to != null) seekSource(payload.seek_to);
      let note = document.querySelector("[data-created-segment-next]");
      if (!(note instanceof HTMLElement)) {
        note = document.createElement("p");
        note.className = "small mb-2";
        note.dataset.testid = "created-segment-next";
        note.setAttribute("data-created-segment-next", "");
        form.before(note);
      }
      note.replaceChildren();
      note.append(document.createTextNode(`${payload.notice} `));
      if (payload.workspace_url) {
        const link = document.createElement("a");
        link.href = payload.workspace_url;
        link.textContent = form.dataset.workspaceLabel || "Open workspace";
        note.append(link);
      }
      if (status instanceof HTMLElement) {
        status.hidden = false;
        status.textContent = payload.message || "";
      }
      document.querySelector("[data-created-segment]")?.scrollIntoView({ block: "nearest" });
    } catch (error) {
      if (status instanceof HTMLElement) {
        status.hidden = false;
        status.textContent = error instanceof Error ? error.message : "Could not create the segment.";
      }
    } finally {
      if (button instanceof HTMLButtonElement) {
        button.disabled = false;
        button.textContent = idle;
      }
    }
  });
}

window.onYouTubeIframeAPIReady = function onYouTubeIframeAPIReady() {
  const root = document.querySelector("[data-source-workspace]");
  const mount = document.getElementById("yt-player");
  if (!(root instanceof HTMLElement) || !(mount instanceof HTMLElement)) return;
  const videoId = root.getAttribute("data-youtube-id");
  if (!videoId) return;

  const seek = Number(root.getAttribute("data-seek-to"));
  const hasSeek = Number.isFinite(seek) && seek > 0;
  ytPlayer = new window.YT.Player("yt-player", {
    videoId,
    playerVars: {
      rel: 0,
      modestbranding: 1,
      enablejsapi: 1,
      controls: 1,
      playsinline: 1,
      ...(hasSeek ? { start: Math.floor(seek) } : {}),
    },
    events: {
      onReady: () => {
        ytReady = true;
      },
    },
  });
};

setupButtons();
setupShortcuts();

// If the IFrame API script already finished before this module ran.
if (window.YT && typeof window.YT.Player === "function" && !ytPlayer) {
  window.onYouTubeIframeAPIReady();
}
