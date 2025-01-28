import {
  DarkTheme,
  DefaultTheme,
  ThemeProvider,
} from "@react-navigation/native";
import { useFonts } from "expo-font";
import { Stack } from "expo-router";
import * as SplashScreen from "expo-splash-screen";
import { StatusBar } from "expo-status-bar";
import { useEffect, useState } from "react";
import "react-native-reanimated";
import { AppProvider } from "./AppContext";
import * as SecureStore from "expo-secure-store";

import { useColorScheme } from "@/hooks/useColorScheme";
import React from "react";
import { Modal } from "react-native";
import LoginPage from "../components/login_page";

// Prevent the splash screen from auto-hiding before asset loading is complete.
SplashScreen.preventAutoHideAsync();

export default function RootLayout() {
  const colorScheme = useColorScheme();
  const [loaded] = useFonts({
    SpaceMono: require("../assets/fonts/SpaceMono-Regular.ttf"),
  });

  const [isLoggedIn, setIsLoggedIn] = useState(false);
  const [modalVisible, setModalVisible] = useState(true);
  const [username, setUsername] = useState("");

  const checkLoginStatus = async () => {
    try {
      const storedUsername = await SecureStore.getItemAsync("employee_id");
      if (storedUsername) {
        setIsLoggedIn(true);
      } else {
        setIsLoggedIn(false);
        setModalVisible(true); // Show login modal if not logged in
      }
    } catch (error) {
      console.error("Error checking login status", error);
      setIsLoggedIn(false);
      setModalVisible(true); // Show login modal on error too
    }
  };

  useEffect(() => {
    if (loaded) {
      SplashScreen.hideAsync();
    }
    checkLoginStatus();
  }, [loaded]);

  useEffect(() => {
    if (isLoggedIn) {
      // Navigate to the tab layout after login is confirmed
      setModalVisible(false); // Hide the modal after successful login
    }
  }, [isLoggedIn]);

  if (!loaded) {
    return null;
  }

  return (
    <AppProvider>
      <ThemeProvider value={colorScheme === "dark" ? DarkTheme : DefaultTheme}>
        {isLoggedIn ? (
          <>
            <Stack>
              <Stack.Screen name="(tabs)" options={{ headerShown: false }} />
              <Stack.Screen
                name="bluetooth_services"
                options={{ title: "Bluetooth Services" }}
              />
              <Stack.Screen
                name="location_services"
                options={{ title: "Location Services" }}
              />
              <Stack.Screen
                name="user_dashboard"
                options={{ title: "Dashboard" }}
              />
              <Stack.Screen name="+not-found" />
            </Stack>
            <StatusBar style="auto" />
          </>
        ) : (
          <Modal
            visible={!isLoggedIn}
            animationType="slide"
            transparent={false}
          >
            <LoginPage setIsLoggedIn={setIsLoggedIn}/>
          </Modal>
        )}
      </ThemeProvider>
    </AppProvider>
  );
}
