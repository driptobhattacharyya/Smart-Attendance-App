import {
  Image,
  StyleSheet,
  Platform,
  Text,
  View,
  TouchableOpacity,
  Alert,
} from "react-native";
import MapView, { Marker, Circle, PROVIDER_GOOGLE } from "react-native-maps";
import { Redirect, useNavigation } from "expo-router";
import * as Location from "expo-location";
import { SafeAreaView } from "react-native-safe-area-context";
import { useEffect, useMemo, useRef, useState, useContext } from "react";
import haversine from "haversine-distance";
import { AppContext } from "@/context/AppContext";

export default function LocationServices() {
  const { handleEntry, handleExit, locationPermissions, office } = useContext(AppContext);
  const [locationPermission, setLocationPermission] = useState(false);
  // The office comes from the profile (/people/me -> /places/offices/{id}).
  const officeLocation = useMemo(
    () => ({
      latitude: office?.latitude ?? 0,
      longitude: office?.longitude ?? 0,
      radius: office?.radius_m ?? 0,
      name: office?.name || "Office",
    }),
    [office]
  );

  // Watch the phone's position and tell the app when we cross the office boundary.
  // The server re-checks the distance on check-in, so this only decides *when* to ask.
  useEffect(() => {
    // Permission is requested (with an explanation) by PermissionGate on the Home tab.
    if (!locationPermissions.foreground) return;
    if (!officeLocation.latitude || !officeLocation.longitude || !officeLocation.radius) {
      return;
    }
    let subscription: Location.LocationSubscription | null = null;
    let wasInside: boolean | null = null;
    let cancelled = false;

    (async () => {
      if (cancelled) return;
      setLocationPermission(true);
      subscription = await Location.watchPositionAsync(
        { accuracy: Location.Accuracy.High, timeInterval: 5000, distanceInterval: 5 },
        (location) => {
          const coords = location.coords;
          const distance = haversine(
            { latitude: officeLocation.latitude, longitude: officeLocation.longitude },
            coords
          );
          const inside = distance <= officeLocation.radius;
          if (inside && wasInside !== true) {
            handleEntry(location);
          } else if (!inside && wasInside !== false) {
            handleExit(location);
          }
          wasInside = inside;
        }
      );
      if (cancelled) subscription.remove();
    })();

    return () => {
      cancelled = true;
      subscription?.remove();
    };
  }, [officeLocation, locationPermissions.foreground, handleEntry, handleExit]);
  
  const mapRef = useRef<MapView>(null);
  const navigation = useNavigation();
  useEffect(() => {
    navigation.setOptions({
      headerRight: () => (
        <TouchableOpacity onPress={focusMap}>
          <View style={{ padding: 10 }}>
            <Text>Focus</Text>
          </View>
        </TouchableOpacity>
      ),
    });
  }, []);

  const focusMap = () => {
    if (!officeLocation.latitude || !officeLocation.longitude) {
      return;
    }

    const officeRegion = {
      latitude: officeLocation.latitude,
      longitude: officeLocation.longitude,
      latitudeDelta: 0.01,
      longitudeDelta: 0.01,
    };
    mapRef.current?.animateCamera(
      { center: officeRegion, zoom: 15 },
      { duration: 3000 }
    );
  };

  if (!officeLocation.latitude || !officeLocation.longitude) {
    return (
      <View style={styles.loadingContainer}>
        <Text>{office ? "Loading office location..." : "No office is assigned to you yet. Ask your admin."}</Text>
      </View>
    );
  }

  return (
    <SafeAreaView style={{ flex: 1 }}>
      <View style={{ flex: 1, paddingTop: 20 }}>
        {locationPermission && (
          <MapView
            style={StyleSheet.absoluteFill}
            provider={PROVIDER_GOOGLE}
            initialRegion={{
              latitude: officeLocation.latitude,
              longitude: officeLocation.longitude,
              latitudeDelta: 0.05,
              longitudeDelta: 0.05,
            }}
            showsCompass
            showsUserLocation
            showsMyLocationButton
            ref={mapRef}
          >
            <Circle
              center={{ latitude: officeLocation.latitude, longitude: officeLocation.longitude }}
              radius={officeLocation.radius}
              strokeWidth={1}
              strokeColor={"#1a66ff"}
              fillColor={"rgba(230,238,255,0.5)"}
            />
            <Marker
              coordinate={{ latitude: officeLocation.latitude, longitude: officeLocation.longitude }}
              title={officeLocation.name}
              description="Your office location"
            />
          </MapView>
        )}
      </View>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  loadingContainer: {
    flex: 1,
    justifyContent: 'center',
    alignItems: 'center',
    backgroundColor: '#F8F4FF',
  },
  titleContainer: {
    flexDirection: "row",
    alignItems: "center",
    gap: 8,
  },
  stepContainer: {
    gap: 8,
    marginBottom: 8,
  },
});
