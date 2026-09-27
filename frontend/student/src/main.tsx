import React from "react";
import ReactDOM from "react-dom/client";
import "../../shared/tokens.css";
import "./styles.css";
import "../../shared/login-refined.css";
import "./refined.css";
import "./lobby.css";
import { App } from "./App";

ReactDOM.createRoot(document.getElementById("root")!).render(<React.StrictMode><App /></React.StrictMode>);
