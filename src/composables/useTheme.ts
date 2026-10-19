import { ref } from "vue";
const theme = ref<"light" | "dark">(localStorage.getItem("fh-theme") === "dark" ? "dark" : "light");
document.documentElement.dataset.theme = theme.value;
export function useTheme() {
    function applyTheme(value: string) {
        if (value !== "light" && value !== "dark")
            return;
        theme.value = value;
        document.documentElement.dataset.theme = value;
        localStorage.setItem("fh-theme", value);
    }
    function toggleTheme() { applyTheme(theme.value === "light" ? "dark" : "light"); }
    return { theme, applyTheme, toggleTheme };
}
