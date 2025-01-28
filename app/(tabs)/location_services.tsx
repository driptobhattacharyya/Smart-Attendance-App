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
// import * as FileSystem from "expo-file-system";
import { AppContext } from "../AppContext"; // Import AppContext

const INITIAL_REGION = {
  latitude: 22.44368,
  longitude: 88.415173,
  latitudeDelta: 0.05,
  longitudeDelta: 0.05,
};
const CENTER_COORDINATE = { latitude: 22.44368, longitude: 88.415173 };
const RADIUS = 1000;

export default function LocationServices() {
  const {handleEntry, handleExit } = useContext(AppContext);
  const [locationPermission, setLocationPermission] = useState(false);
  const [isInsideCircle, setIsInsideCircle] = useState(false);

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
  }, []);

  const [entryTime, setEntryTime] = useState<any | null>(null);
  const [exitTime, setExitTime] = useState<any | null>(null);

  const startLocationTracking = () => {
    let wasInsideCircle = false;
    let localEntryTime = null;

    setInterval(async () => {
      try {
        const location = await Location.getCurrentPositionAsync({});
        const userLocation = {
          latitude: location.coords.latitude,
          longitude: location.coords.longitude,
        };
        
        const distanceFromCenter = haversine(CENTER_COORDINATE, userLocation);
        const isCurrentlyInside = distanceFromCenter <= RADIUS;

        if (isCurrentlyInside && !wasInsideCircle) {
          // User just entered the circle
          const entryTimestamp = new Date();
          // setEntryTime(entryTimestamp.getTime());
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
          // setEntryTime(null);
          // setExitTime(null);
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
    const FIEM = {
      latitude: 22.44368,
      longitude: 88.415173,
      latitudeDelta: 0.01,
      longitudeDelta: 0.01,
    };
    mapRef.current?.animateCamera(
      { center: FIEM, zoom: 15 },
      { duration: 3000 }
    );
  };

  return (
    <SafeAreaView style={{ flex: 1 }}>
      <View style={{ flex: 1, paddingTop: 20 }}>
        {locationPermission && (
          <MapView
            style={StyleSheet.absoluteFillObject}
            provider={PROVIDER_GOOGLE}
            initialRegion={INITIAL_REGION}
            showsCompass
            showsUserLocation
            showsMyLocationButton
            ref={mapRef}
          >
            <Circle
              center={CENTER_COORDINATE}
              radius={RADIUS}
              strokeWidth={1}
              strokeColor={"#1a66ff"}
              fillColor={"rgba(230,238,255,0.5)"}
            />
            <Marker
              coordinate={CENTER_COORDINATE}
              title="FIEM"
              description="Testiing testing!"
            />
          </MapView>
        )}
      </View>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
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
