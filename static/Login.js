const loginForm = document.getElementById("loginForm");
const identifierInput = document.getElementById("login-identifier");
const passwordInput = document.getElementById("login-password");
const togglePassword = document.getElementById("toggle-password");
const loginButton = document.getElementById("login-submit");
const loginStatus = document.getElementById("login-status");
const cooldowns = new Map();
let loginPending = false;
let displayedCooldown = false;

togglePassword.addEventListener("click", () => {
    const isVisible = passwordInput.type === "text";
    passwordInput.type = isVisible ? "password" : "text";
    togglePassword.setAttribute("aria-pressed", String(!isVisible));
    togglePassword.setAttribute("aria-label", isVisible ? "Show password" : "Hide password");
    togglePassword.innerHTML = `<i class="fa-solid fa-eye${isVisible ? "" : "-slash"}"></i>`;
});

function updateCooldown() {
    const key = identifierInput.value.trim().toLowerCase();
    const remaining = Math.max(0, Math.ceil(((cooldowns.get(key) || 0) - Date.now()) / 1000));
    loginButton.disabled = loginPending || remaining > 0;
    loginButton.textContent = remaining ? `Try again in ${remaining}s` : "Login";
    if (remaining) {
        loginStatus.textContent = `Too many failed attempts. Try again in ${remaining} seconds.`;
        displayedCooldown = true;
    } else if (displayedCooldown) {
        loginStatus.textContent = "You can try signing in again.";
        displayedCooldown = false;
    }
}

identifierInput.addEventListener("input", updateCooldown);
window.setInterval(updateCooldown, 1000);

async function postAuth(path, payload) {
    const response = await fetch(`/api/v1${path}`, {
        method: "POST",
        credentials: "same-origin",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify(payload)
    });
    const result = await response.json();
    return result;
}

loginForm.addEventListener("submit", async event => {
    event.preventDefault();
    if (loginButton.disabled) return;
    const username = identifierInput.value.trim();
    loginPending = true;
    updateCooldown();
    loginStatus.textContent = "Signing in...";
    try {
        const data = await postAuth("/login", {username, password: passwordInput.value});
        if (data.success) {
            window.location.href = data.admin ? "/admin/dashboard" : "/home";
            return;
        }
        loginStatus.textContent = data.message || "Sign-in failed.";
        if (data.code === "admin_locked" && Number.isFinite(data.retry_after)) {
            cooldowns.set(username.toLowerCase(), Date.now() + data.retry_after * 1000);
        }
    } catch (error) {
        console.error("Login request failed:", error);
        loginStatus.textContent = "Could not reach the server. Please try again.";
    } finally {
        loginPending = false;
        updateCooldown();
    }
});

const recoveryDialog = document.getElementById("admin-recovery-dialog");
const recoveryForm = document.getElementById("recovery-form");
const recoveryButton = document.getElementById("recovery-submit");
const recoveryStatus = document.getElementById("recovery-status");
let recoveryMode = "forgot";
let recoveryToken = "";

function openRecovery(mode, token = "") {
    recoveryMode = mode;
    recoveryToken = token;
    recoveryForm.reset();
    recoveryStatus.textContent = "";
    recoveryButton.hidden = false;
    recoveryButton.disabled = false;
    const forgot = mode === "forgot";
    const reset = mode === "reset";
    document.getElementById("recovery-title").textContent = forgot ? "Forgot password?" : reset ? "Reset admin password" : "Verify recovery Gmail";
    document.getElementById("recovery-description").textContent = forgot
        ? "Admin accounts only. Enter your username, sign-in email, or verified recovery Gmail. We'll send the reset link to your verified recovery Gmail."
        : reset ? "Maximum 72 UTF-8 bytes."
        : "Confirm this Gmail as your admin account's recovery address. Only continue if you requested this change.";
    document.getElementById("recovery-identifier-field").hidden = !forgot;
    document.getElementById("recovery-password-fields").hidden = !reset;
    document.getElementById("recovery-identifier").disabled = !forgot;
    document.getElementById("recovery-identifier").required = forgot;
    document.getElementById("recovery-identifier").value = forgot ? identifierInput.value : "";
    ["recovery-password", "recovery-confirm"].forEach(id => {
        document.getElementById(id).disabled = !reset;
        document.getElementById(id).required = reset;
    });
    recoveryButton.textContent = forgot ? "Send reset link" : reset ? "Reset password" : "Verify Gmail";
    if (!recoveryDialog.open) recoveryDialog.showModal();
}

document.getElementById("forgot-password").addEventListener("click", () => openRecovery("forgot"));
document.getElementById("recovery-close").addEventListener("click", () => {
    if (loginForm.hidden) {
        window.location.assign("/login");
    } else {
        recoveryDialog.close();
    }
});
recoveryDialog.addEventListener("close", () => {
    recoveryToken = "";
    document.querySelector(".admin-login").open = true;
    identifierInput.focus();
});

recoveryForm.addEventListener("submit", async event => {
    event.preventDefault();
    if (recoveryButton.disabled) return;
    recoveryButton.disabled = true;
    recoveryStatus.textContent = "Please wait...";
    const mode = recoveryMode;
    const paths = {forgot: "/admin/forgot-password", verify: "/admin/verify-recovery", reset: "/admin/reset-password"};
    const payload = Object.fromEntries(new FormData(recoveryForm).entries());
    if (mode !== "forgot") payload.token = recoveryToken;
    try {
        const result = await postAuth(paths[mode], payload);
        recoveryStatus.textContent = result.message || "Request failed.";
        if (result.success && mode !== "forgot") {
            recoveryButton.hidden = true;
            recoveryToken = "";
            if (mode === "verify") {
                if (result.return_to_dashboard) {
                    window.location.replace("/admin/dashboard#account-security");
                    return;
                }
                document.getElementById("recovery-link-help").hidden = true;
                loginForm.hidden = false;
                document.title = "PathFinder Login";
                window.history.replaceState(null, "", "/login");
                recoveryDialog.close();
                loginStatus.textContent = `${result.message} Sign in to continue.`;
                return;
            }
            if (mode === "reset") {
                cooldowns.clear();
                updateCooldown();
            }
        }
    } catch (error) {
        console.error("Account recovery request failed:", error);
        recoveryStatus.textContent = "Could not reach the server. Please try again.";
    } finally {
        recoveryButton.disabled = false;
    }
});

function readRecoveryFragment() {
    const fragment = new URLSearchParams(window.location.hash.slice(1));
    const mode = fragment.has("reset") ? "reset" : fragment.has("verify") ? "verify" : "";
    if (mode) {
        const token = fragment.get(mode);
        window.history.replaceState(null, "", window.location.pathname + window.location.search);
        openRecovery(mode, token);
    }
}

window.addEventListener("hashchange", readRecoveryFragment);
readRecoveryFragment();
