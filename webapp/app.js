(() => {
  const tg = window.Telegram?.WebApp;
  const elements = {
    state: document.getElementById("connection-state"),
    welcome: document.getElementById("welcome-name"),
    miningStatus: document.getElementById("mining-status"),
    balance: document.getElementById("balance"),
    mined: document.getElementById("mined"),
    cap: document.getElementById("mining-cap"),
    speed: document.getElementById("speed"),
    rate: document.getElementById("base-rate"),
    mine: document.getElementById("mine-button"),
    action: document.getElementById("action-message"),
    close: document.getElementById("close-button"),
  };

  function setState(type) {
    elements.state.className = `status-dot ${type || ""}`;
  }

  function message(text, isError = false) {
    elements.action.textContent = text;
    elements.action.style.color = isError ? "#ff7d91" : "";
  }

  async function request(path) {
    const response = await fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ initData: tg?.initData || "" }),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(data.error || "ارتباط با وب‌اپ برقرار نشد.");
    }
    return data;
  }

  function showProfile(profile) {
    elements.balance.textContent = profile.balance;
    elements.mined.textContent = profile.total_mined;
    elements.cap.textContent = `از ${profile.mining_cap} TRON`;
    elements.speed.textContent = `×${profile.speed_multiplier}`;
    elements.rate.textContent = profile.base_rate;
    elements.miningStatus.textContent = profile.mining_status;
    elements.mine.disabled = profile.mining_status.includes("در انتظار") || profile.mining_status.includes("آماده برداشت وجه");
  }

  async function loadProfile() {
    if (!tg?.initData) {
      setState("error");
      elements.welcome.textContent = "این صفحه را از داخل تلگرام باز کنید.";
      message("دسترسی امن وب‌اپ فقط در تلگرام فعال است.", true);
      return;
    }
    try {
      const profile = await request("/api/profile");
      const name = tg.initDataUnsafe?.user?.first_name || profile.first_name || "کاربر";
      elements.welcome.textContent = `سلام ${name}`;
      showProfile(profile);
      setState("online");
      message("وضعیت حساب به‌روز است.");
    } catch (error) {
      setState("error");
      elements.welcome.textContent = "دسترسی به حساب ممکن نیست.";
      message(error.message, true);
    }
  }

  elements.mine.addEventListener("click", async () => {
    elements.mine.disabled = true;
    message("در حال ثبت برداشت ماین…");
    try {
      const data = await request("/api/mine");
      if (data.profile) {
        showProfile(data.profile);
      }
      const messages = {
        claimed: `این نوبت: ${data.amount} TRON ثبت شد.`,
        too_soon: `هنوز ${data.remaining_seconds} ثانیه باقی مانده است.`,
        cap_reached: "سقف ماین کامل است؛ از ربات درخواست برداشت ثبت کنید.",
        withdrawal_pending: "درخواست برداشت شما در انتظار بررسی است.",
      };
      message(messages[data.status] || "وضعیت حساب به‌روز شد.");
    } catch (error) {
      message(error.message, true);
    } finally {
      if (!elements.miningStatus.textContent.includes("در انتظار") && !elements.miningStatus.textContent.includes("آماده برداشت وجه")) {
        elements.mine.disabled = false;
      }
    }
  });

  elements.close.addEventListener("click", () => {
    if (tg) {
      tg.close();
    } else {
      window.history.back();
    }
  });

  if (tg) {
    tg.ready();
    tg.expand();
    try {
      tg.setHeaderColor("#09090b");
      tg.setBackgroundColor("#09090b");
    } catch (_) {
      // نسخه‌های قدیمی تلگرام ممکن است این APIها را نداشته باشند.
    }
  }
  loadProfile();
})();
