const loginForm = document.getElementById("loginForm");
const passwordInput = document.getElementById("login-password");
const togglePassword = document.getElementById("toggle-password");

if (passwordInput && togglePassword) {
    togglePassword.addEventListener("click", () => {
        const isVisible = passwordInput.type === "text";
        passwordInput.type = isVisible ? "password" : "text";
        togglePassword.setAttribute("aria-pressed", String(!isVisible));
        togglePassword.setAttribute("aria-label", isVisible ? "Show password" : "Hide password");
        togglePassword.innerHTML = `<i class="fa-solid fa-eye${isVisible ? "" : "-slash"}"></i>`;
    });
}

if (loginForm) {
    loginForm.addEventListener("submit", (event) => {
        event.preventDefault();
        loginUser();
    });
}

function loginUser(){

    let email = document.getElementById("login-email").value;
    let password = document.getElementById("login-password").value;


    fetch("/login", {
        method: "POST",
        credentials: "same-origin",
        headers:{
            "Content-Type":"application/json"
        },
        body: JSON.stringify({
            email: email,
            password: password
        })
    })

    .then(response => response.json())

    .then(data => {

        if(data.success){
            if(data.admin){
                if (data.semi_admin) {
                    window.location.href = "/admin/dashboard";
                } else {
                    window.location.href = "/admin/dashboard";
                }
            } else {
                window.location.href = "/home";
            }
        }
        else{
            alert(data.message);
        }

    })

    .catch(error => {
        console.log(error);
        alert("Login request failed. Make sure the app is open from http://localhost:5000.");
    });

}