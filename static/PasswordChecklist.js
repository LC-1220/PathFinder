(() => {
    const rules = [
        ["At least 8 characters", value => [...value].length >= 8],
        ["One uppercase letter", value => /\p{Uppercase}/u.test(value)],
        ["One lowercase letter", value => /\p{Lowercase}/u.test(value)],
        ["One number", value => /[0-9]/.test(value)],
        ["One special character (e.g. !@#$%)", value => /[^\p{L}\p{N}\s]/u.test(value)]
    ];
    const encoder = new TextEncoder();

    document.querySelectorAll('input[type="password"]').forEach((input, index) => {
        if (input.id === "login-password") return;
        if (!input.id) input.id = `password-field-${index}`;
        const wrapper = document.createElement("div");
        wrapper.className = "password-visibility-field";
        const button = document.createElement("button");
        button.type = "button";
        button.className = "password-visibility-toggle";
        button.setAttribute("aria-controls", input.id);
        button.setAttribute("aria-label", "Show password");
        button.setAttribute("aria-pressed", "false");
        const icon = document.createElement("i");
        icon.className = "fa-solid fa-eye";
        icon.setAttribute("aria-hidden", "true");
        button.append(icon);
        input.before(wrapper);
        wrapper.append(input, button);
        const hide = () => {
            input.type = "password";
            button.setAttribute("aria-label", "Show password");
            button.setAttribute("aria-pressed", "false");
            icon.className = "fa-solid fa-eye";
        };
        button.addEventListener("click", () => {
            if (input.type === "text") {
                hide();
            } else {
                input.type = "text";
                button.setAttribute("aria-label", "Hide password");
                button.setAttribute("aria-pressed", "true");
                icon.className = "fa-solid fa-eye-slash";
            }
        });
        input.form?.addEventListener("reset", hide);
        input.closest("dialog")?.addEventListener("close", hide);
    });

    document.querySelectorAll("input[data-password-checklist]").forEach((input, index) => {
        const checklist = document.createElement("ul");
        checklist.className = "password-checklist";
        checklist.id = `password-requirements-${index}`;
        checklist.setAttribute("aria-label", "Password requirements");
        const items = rules.map(([label]) => {
            const item = document.createElement("li");
            item.className = "password-rule";
            const icon = document.createElement("span");
            icon.className = "password-rule-icon";
            icon.setAttribute("aria-hidden", "true");
            const text = document.createElement("span");
            text.textContent = label;
            item.append(icon, text);
            checklist.append(item);
            return item;
        });
        input.closest(".password-visibility-field").insertAdjacentElement("afterend", checklist);
        input.setAttribute("aria-describedby", [input.getAttribute("aria-describedby"), checklist.id].filter(Boolean).join(" "));

        const update = () => {
            const results = rules.map(([, test]) => test(input.value));
            items.forEach((item, ruleIndex) => {
                const met = results[ruleIndex];
                item.classList.toggle("is-met", met);
                item.firstElementChild.textContent = met ? "\u2713" : "\u00d7";
                item.setAttribute("aria-label", `${met ? "Met" : "Not met"}: ${rules[ruleIndex][0]}`);
            });
            const tooLong = encoder.encode(input.value).length > 72;
            input.setCustomValidity(!input.value ? "" : tooLong
                ? "Password must be no more than 72 UTF-8 bytes."
                : results.every(Boolean) ? "" : "Meet all five password requirements.");
        };
        input.addEventListener("input", update);
        input.addEventListener("change", update);
        input.form?.addEventListener("reset", () => window.setTimeout(update, 0));
        update();
    });
})();
