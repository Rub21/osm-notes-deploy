import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App.jsx";
import Stats from "./Stats.jsx";
import "./styles.css";

createRoot(document.getElementById("root")).render(<StrictMode>{location.pathname.startsWith("/stats") ? <Stats /> : <App />}</StrictMode>);
