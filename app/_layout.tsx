import {
  DarkTheme,
  DefaultTheme,
  ThemeProvider,
} from "@react-navigation/native";
import { useFonts } from "expo-font";
import { Stack } from "expo-router";
import * as SplashScreen from "expo-splash-screen";
import { StatusBar } from "expo-status-bar";
import { useEffect, useContext } from "react";
import "react-native-reanimated";
import { AppContext, AppProvider } from "./AppContext";
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
  const { isLoggedIn, setIsLoggedIn } = context;
  const router = useRouter();

  const checkLoginStatus = async () => {
    try {
      const storedUsername = await SecureStore.getItemAsync("employee_id");
      setIsLoggedIn(!!storedUsername);
    } catch (error) {
      console.error("Error checking login status", error);
      setIsLoggedIn(false);
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
            <Stack.Screen
              name="bluetooth_services"
              options={{ headerShown: true, title: "Bluetooth Services" }}
            />
            <Stack.Screen
              name="location_services"
              options={{ headerShown: true, title: "Location Services" }}
            />
            <Stack.Screen
              name="user_dashboard"
              options={{ headerShown: true, title: "Dashboard" }}
            />
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
