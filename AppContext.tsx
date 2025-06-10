import React, { createContext, useState } from "react";

export type AppContextType = {
  handleEntry: () => void;
  handleExit: () => void;
};

export const AppContext = createContext<AppContextType | undefined>(undefined);

export const AppProvider = ({ children }: { children: React.ReactNode }) => {
  const handleEntry = () => {
    // Handle entry logic
    console.log("Entry recorded");
  };

  const handleExit = () => {
    // Handle exit logic
    console.log("Exit recorded");
  };

  return (
    <AppContext.Provider value={{ handleEntry, handleExit }}>
      {children}
    </AppContext.Provider>
  );
}; 