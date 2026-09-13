"use client";

import { createContext, useCallback, useContext, useMemo, useState, ReactNode } from "react";

interface ActivePatient {
  hadmId: number | null;
  subjectId: number | null;
  asOfHours: number | null;
  headline: string | null;
}

interface ActivePatientContextValue extends ActivePatient {
  setActivePatient: (p: Partial<ActivePatient>) => void;
  clearActivePatient: () => void;
}

const ActivePatientContext = createContext<ActivePatientContextValue | null>(null);

export function ActivePatientProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<ActivePatient>({
    hadmId: null,
    subjectId: null,
    asOfHours: null,
    headline: null,
  });

  const setActivePatient = useCallback((p: Partial<ActivePatient>) => {
    setState((prev) => ({ ...prev, ...p }));
  }, []);

  const clearActivePatient = useCallback(() => {
    setState({ hadmId: null, subjectId: null, asOfHours: null, headline: null });
  }, []);

  const value = useMemo(
    () => ({ ...state, setActivePatient, clearActivePatient }),
    [state, setActivePatient, clearActivePatient]
  );

  return <ActivePatientContext.Provider value={value}>{children}</ActivePatientContext.Provider>;
}

export function useActivePatient() {
  const ctx = useContext(ActivePatientContext);
  if (!ctx) throw new Error("useActivePatient must be used within ActivePatientProvider");
  return ctx;
}
