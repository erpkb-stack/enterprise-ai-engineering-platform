import { create } from "zustand";

export type Me = {
  id: string;
  email: string;
  full_name: string;
  organization_id: string;
  organization: { id: string; name: string; slug: string; is_demo: boolean } | null;
  roles: string[];
  permissions: string[];
  teams: { id: string; name: string }[];
};

type AuthState = {
  accessToken: string | null;
  refreshToken: string | null;
  me: Me | null;
  setTokens: (a: string, r: string) => void;
  setMe: (m: Me | null) => void;
  logout: () => void;
  can: (perm: string) => boolean;
};

const RT_KEY = "aiecc.rt";
const readRt = () => {
  try {
    return sessionStorage.getItem(RT_KEY);
  } catch {
    return null;
  }
};

export const useAuth = create<AuthState>((set, get) => ({
  accessToken: null,
  refreshToken: readRt(),
  me: null,
  setTokens: (a, r) => {
    try {
      sessionStorage.setItem(RT_KEY, r);
    } catch {
      /* storage unavailable: session lasts for page lifetime only */
    }
    set({ accessToken: a, refreshToken: r });
  },
  setMe: (me) => set({ me }),
  logout: () => {
    try {
      sessionStorage.removeItem(RT_KEY);
    } catch {
      /* ignore */
    }
    set({ accessToken: null, refreshToken: null, me: null });
  },
  can: (perm) => !!get().me?.permissions.includes(perm),
}));

type Theme = "light" | "dark";
type UiState = { theme: Theme; toggleTheme: () => void; sidebarOpen: boolean; setSidebar: (v: boolean) => void };

const initialTheme = (): Theme => {
  try {
    const t = localStorage.getItem("aiecc.theme");
    if (t === "light" || t === "dark") return t;
  } catch {
    /* ignore */
  }
  return typeof window !== "undefined" && window.matchMedia?.("(prefers-color-scheme: dark)").matches ? "dark" : "light";
};

export const useUi = create<UiState>((set, get) => ({
  theme: initialTheme(),
  toggleTheme: () => {
    const t = get().theme === "dark" ? "light" : "dark";
    try {
      localStorage.setItem("aiecc.theme", t);
    } catch {
      /* ignore */
    }
    set({ theme: t });
  },
  sidebarOpen: false,
  setSidebar: (v) => set({ sidebarOpen: v }),
}));
