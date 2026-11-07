import { useFonts } from "expo-font";
import { DarkTheme, DefaultTheme, Stack, ThemeProvider } from "expo-router";
import * as SplashScreen from "expo-splash-screen";
import { StatusBar } from "expo-status-bar";
import { useEffect, useContext } from "react";
import "react-native-reanimated";
// Registers the background presence task (must run at startup, before any component mounts).
import "@/lib/presence";
import { AppContext, AppProvider } from "@/context/AppContext";
import ConsentScreen from "@/components/ConsentScreen";
import { ApiError, apiFetch, clearSession, loadStoredConsentRequired, TOKEN_KEY } from "@/lib/api";
import { API_CONFIG } from "@/lib/config";
import * as SecureStore from "expo-secure-store";
import { useColorScheme } from "@/hooks/useColorScheme";
import React from "react";
import { useRouter } from "expo-router";

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
  const { isLoggedIn, setIsLoggedIn, consentRequired, setConsentRequired } = context;
  const router = useRouter();

  const checkLoginStatus = async () => {
    try {
      const token = await SecureStore.getItemAsync(TOKEN_KEY);
      if (!token) {
        setIsLoggedIn(false);
        return;
      }
      // Make sure the saved token is still valid (it expires after a while).
      const me = await apiFetch(API_CONFIG.ENDPOINTS.ME);
      setConsentRequired(Boolean(me?.consent_required));
      setIsLoggedIn(true);
    } catch (error) {
      if (error instanceof ApiError && error.status === 401) {
        await clearSession();
        setIsLoggedIn(false);
      } else {
        // Offline: keep the user signed in; requests will retry later. Use the last known consent state.
        setConsentRequired(await loadStoredConsentRequired());
        setIsLoggedIn(!!(await SecureStore.getItemAsync(TOKEN_KEY)));
      }
    }
  };

  useEffect(() => {
    if (loaded) {
      SplashScreen.hideAsync();
    }
    checkLoginStatus();
  }, [loaded]);

  if (!loaded) {
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

  return (
    <ThemeProvider value={colorScheme === "dark" ? DarkTheme : DefaultTheme}>
      <Stack screenOptions={{ headerShown: false }}>
        {!isLoggedIn ? (
          // Auth screens
          <>
            <Stack.Screen name="index" />
            <Stack.Screen name="signup" />
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
