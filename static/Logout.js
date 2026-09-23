console.log("Logout.js loaded");

function openLogout() {
    console.log("openLogout function called");
    document.getElementById("logoutPopup").style.display = "flex";
}

function closeLogout() {
    document.getElementById("logoutPopup").style.display = "none";
}