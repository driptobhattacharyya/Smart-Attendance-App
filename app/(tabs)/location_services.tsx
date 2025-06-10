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
import { useEffect, useRef, useState, useContext } from "react";
import haversine from "haversine-distance";
import * as SecureStore from "expo-secure-store";
import { AppContext } from "../AppContext";

export default function LocationServices() {
  const { handleEntry, handleExit } = useContext(AppContext);
  const [locationPermission, setLocationPermission] = useState(false);
  const [isInsideCircle, setIsInsideCircle] = useState(false);
  const [officeLocation, setOfficeLocation] = useState({
    latitude: 0,
    longitude: 0,
    radius: 0,
    name: '',
  });

  useEffect(() => {
    const loadOfficeLocation = async () => {
      try {
        const [latitude, longitude, radius, name] = await Promise.all([
          SecureStore.getItemAsync("office_latitude"),
          SecureStore.getItemAsync("office_longitude"),
          SecureStore.getItemAsync("office_radius"),
          SecureStore.getItemAsync("office_name"),
        ]);

        if (latitude && longitude && radius) {
          setOfficeLocation({
            latitude: parseFloat(latitude),
            longitude: parseFloat(longitude),
            radius: parseFloat(radius),
            name: name || 'Office',
          });
        } else {
          Alert.alert("Error", "Office location not found. Please log in again.");
        }
      } catch (error) {
        console.error("Error loading office location:", error);
        Alert.alert("Error", "Failed to load office location. Please try again.");
      }
    };

    loadOfficeLocation();
  }, []);

  useEffect(() => {
    (async () => {
      let { status } = await Location.requestForegroundPermissionsAsync();
      if (status !== "granted") {
        console.log("Permission to access location was denied");
        return;
      }
      setLocationPermission(true);
      startLocationTracking();
    })();
  }, [officeLocation]);

  const [entryTime, setEntryTime] = useState<any | null>(null);
  const [exitTime, setExitTime] = useState<any | null>(null);

  const startLocationTracking = () => {
    if (!officeLocation.latitude || !officeLocation.longitude || !officeLocation.radius) {
      return;
    }

    let wasInsideCircle = false;
    let localEntryTime = null;

    setInterval(async () => {
      try {
        const location = await Location.getCurrentPositionAsync({});
        const userLocation = {
          latitude: location.coords.latitude,
          longitude: location.coords.longitude,
        };
        
        const distanceFromCenter = haversine(
          { latitude: officeLocation.latitude, longitude: officeLocation.longitude },
          userLocation
        );
        const isCurrentlyInside = distanceFromCenter <= officeLocation.radius;

        if (isCurrentlyInside && !wasInsideCircle) {
          // User just entered the circle
          const entryTimestamp = new Date();
          localEntryTime = entryTimestamp.getTime();
          handleEntry();
          Alert.alert("Entered the circle!");
          console.log("Entered the circle at:", entryTimestamp);
          wasInsideCircle = true;
        } else if (!isCurrentlyInside && wasInsideCircle) {
          // User just exited the circle
          const exitTimestamp = new Date();
          setExitTime(exitTimestamp);

          console.log("entryTime:", entryTime);
          console.log("exitTime:", exitTimestamp);
          handleExit();

          // Reset the state
          wasInsideCircle = false;
          localEntryTime = null;
        }
      } catch (error) {
        console.error("Error fetching location:", error);
      }
    }, 5000); // Checks every 5 seconds
  };
  
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
        <Text>Loading office location...</Text>
      </View>
    );
  }

  return (
    <SafeAreaView style={{ flex: 1 }}>
      <View style={{ flex: 1, paddingTop: 20 }}>
        {locationPermission && (
          <MapView
            style={StyleSheet.absoluteFillObject}
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
