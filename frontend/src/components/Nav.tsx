import { NavLink } from "react-router-dom";

export function Nav() {
  return (
    <nav aria-label="primary" className="nav">
      <NavLink to="/" end>
        Projects
      </NavLink>
      <NavLink to="/import">Import</NavLink>
      <NavLink to="/settings">Settings</NavLink>
    </nav>
  );
}
