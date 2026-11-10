import { useFonts } from "expo-font";
import { DarkTheme, DefaultTheme, Stack, ThemeProvider } from "expo-router";
import * as SplashScreen from "expo-splash-screen";
import { StatusBar } from "expo-status-bar";
import { useEffect, useContext, useState } from "react";
import "react-native-reanimated";
// Registers the background presence task (must run at startup, before any component mounts).
import "@/lib/presence";
import { AppContext, AppProvider } from "@/context/AppContext";
import ConsentScreen from "@/components/ConsentScreen";
import FaceEnrolmentScreen from "@/components/FaceEnrolmentScreen";
import { useColorScheme } from "@/hooks/useColorScheme";
import React from "react";

// Prevent the splash screen from auto-hiding before asset loading is complete.
SplashScreen.preventAutoHideAsync();

function RootLayoutContent() {
  const colorScheme = useColorScheme();
  const [loaded] = useFonts({
    SpaceMono: require("../assets/fonts/SpaceMono-Regular.ttf"),
  });

  const context = useContext(AppContext);
  if (!context) {
    throw new Error('RootLayoutContent must be used within an AppProvider');
  }
  const { isLoggedIn, consentRequired, faceEnrolled, faceRequired, restoreSession } = context;
  const [restored, setRestored] = useState(false);

  // Resume a saved session (refresh token -> access token -> /people/me) before showing anything.
  useEffect(() => {
    restoreSession().finally(() => setRestored(true));
  }, []);

  useEffect(() => {
    if (loaded && restored) {
      SplashScreen.hideAsync();
    }
  }, [loaded, restored]);

  if (!loaded || !restored) {
    return null;
  }

  // Consent comes before anything else for a logged-in user: the tabs (and so the location
  // watcher), the permission gate and Bluetooth stay unmounted until the notice is accepted.
  if (isLoggedIn && consentRequired) {
    return (
      <ThemeProvider value={colorScheme === "dark" ? DarkTheme : DefaultTheme}>
        <ConsentScreen />
        <StatusBar style="auto" />
      </ThemeProvider>
    );
  }

  // After consent, a logged-in user without an enrolled face enrols before the tabs.
  if (isLoggedIn && faceRequired && !faceEnrolled) {
    return (
      <ThemeProvider value={colorScheme === "dark" ? DarkTheme : DefaultTheme}>
        <FaceEnrolmentScreen />
        <StatusBar style="auto" />
      </ThemeProvider>
    );
  }

  return (
    <ThemeProvider value={colorScheme === "dark" ? DarkTheme : DefaultTheme}>
      <Stack screenOptions={{ headerShown: false }}>
        {!isLoggedIn ? (
          // Auth screens
          <>
            <Stack.Screen name="index" />
            <Stack.Screen name="invite" />
            <Stack.Screen name="forgot-password" />
          </>
        ) : (
          // App screens
          <>
            <Stack.Screen name="(tabs)" />
            <Stack.Screen name="+not-found" />
          </>
        )}
      </Stack>
      <StatusBar style="auto" />
    </ThemeProvider>
  );
}

export default function RootLayout() {
  return (
    <AppProvider>
      <RootLayoutContent />
    </AppProvider>
  );
}
