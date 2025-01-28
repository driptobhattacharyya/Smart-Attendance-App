import React, { useContext, useRef, useState } from "react";
import MaterialIcons from "@expo/vector-icons/MaterialIcons";

import {
  View,
  Text,
  TouchableOpacity,
  StyleSheet,
  ActivityIndicator,
} from "react-native";
import {
  Camera,
  CameraType,
  CameraView,
  useCameraPermissions,
} from "expo-camera";
import * as ImagePicker from "expo-image-picker";
import * as FileSystem from "expo-file-system";
import { AppContext } from "@/app/AppContext";
import { get } from "react-native/Libraries/TurboModule/TurboModuleRegistry";
import PhotoPreviewSection from "./PhotoPreviewSection";

const FaceRecog = () => {
  const [isLoading, setIsLoading] = useState(false);
  const { setIsFaceScanned } = useContext(AppContext);
  // const [cameraRef, setCameraRef] = useState(null);
  const [image, setImage] = useState(null);

  const [facing, setFacing] = useState<CameraType>("front");
  const cameraRef = useRef<CameraView | null>(null);
  const [permission, requestPermission] = useCameraPermissions();
  const [photo, setPhoto] = useState<any>(null);

  if (!permission) {
    return <View />; // Camera permissions are still loading.
  }

  if (!permission.granted) {
    return (
      <View style={styles.container}>
        <Text style={styles.message}>
          We need your permission to show the camera
        </Text>
        <TouchableOpacity
          style={styles.permissionButton}
          onPress={requestPermission}
        >
          <Text style={styles.permissionText}>Grant Permission</Text>
        </TouchableOpacity>
      </View>
    );
  }

  function toggleCameraFacing() {
    setFacing((current) => (current === "back" ? "front" : "back"));
  }

  const getImageData = async (uri) => {
    try {
      const base64Data = await FileSystem.readAsStringAsync(uri, {
        encoding: FileSystem.EncodingType.Base64,
      });
      console.log("Image Base64 Data:", base64Data);
    } catch (error) {
      console.error("Error reading image data:", error);
    }
  };

  const handleFaceScan = async () => {
    try {
      if (cameraRef.current) {
        console.log("Ok 0");
        const options = {
          quality: 0.9,
          // base64: true,
          imageType: "jpg",
          exif: false,
        };
        const takePhoto = await cameraRef.current.takePictureAsync(options);
        console.log("Ok 1");
        console.log("Photo:", takePhoto.uri);
        setPhoto(takePhoto);
        setIsLoading(true);

        // Fetch the file as Blob
        const response0 = await fetch(takePhoto.uri);
        const photoBlob = await response0.blob();
        console.log("Blob:", photoBlob);
        
        const formData = new FormData();
        formData.append("image", {
          uri: takePhoto.uri,
          name: "photo.jpg", // Filename
          type: "image/jpeg", // MIME type
        });
        formData.append("person_id", "dafe4ea7-00c0-4265-9a8f-1bcea379a1c0");
        console.log("Ok 2");
        const response = await fetch(
          "https://idx-backendattendance-1890784-k27k4ovjaq-ue.a.run.app/api/face_recog/recognize",
          {
            method: "POST",
            headers: {
              "Content-Type": "multipart/form-data",
            },
            body: formData,
          }
        );
        console.log("Ok 3");
        // console.log("Result:", response.text());
        const result = await response.json();
        console.log("Result:", result);
        if (result.message === "Face recognized") {
          alert("Face recognized successfully!");
          setIsFaceScanned(true);
        } else {
          alert(result.message || "Face recognition failed. Please try again.");
        }
        console.log("Ok 4");
        setIsLoading(false);
      }
    } catch (error) {
      console.error("Error during face recognition:", error);
      alert("An error occurred while processing the image. Please try again.");
    } finally {
      setIsLoading(false);
    }
  };
  
  return (
    <View style={styles.container}>
      <Text style={styles.header}>Face Recognition</Text>
      <Text style={styles.instructions}>
        Position your face in the frame and click the button below to start face
        recognition.
      </Text>
      {isLoading ? (
        <View style={styles.loadingContainer}>
          <ActivityIndicator size="large" color="#442c91" />
          <Text style={styles.loadingText}>Scanning face, please wait...</Text>
        </View>
      ) : (
        <>
          <View style={styles.cameraContainer}>
            <CameraView ref={cameraRef} style={styles.camera} facing={facing}>
              {/* Overlay for face positioning */}
              <View style={styles.overlay}>
                <View style={styles.faceFrame} />
              </View>
            </CameraView>
          </View>
          <View style={styles.controls}>
            <TouchableOpacity
              style={styles.flipButton}
              onPress={toggleCameraFacing}
            >
              <MaterialIcons
                name="flip-camera-android"
                size={28}
                color="white"
              />
            </TouchableOpacity>
            <TouchableOpacity
              style={styles.shutterButton}
              onPress={handleFaceScan}
            >
              <MaterialIcons name="camera" size={45} color="white" />
            </TouchableOpacity>
          </View>
          <Text style={styles.status}>Status: Ready to scan</Text>
        </>
      )}
    </View>
  );
};

const styles = StyleSheet.create({
  container: {
    flex: 1,
    padding: 20,
    backgroundColor: "#f4eefe",
    alignItems: "center",
  },
  message: {
    textAlign: "center",
    paddingBottom: 10,
  },
  permissionButton: {
    marginTop: 10,
    backgroundColor: "#442c91",
    padding: 10,
    borderRadius: 5,
  },
  permissionText: {
    color: "white",
    fontWeight: "bold",
    fontSize: 16,
  },
  header: {
    fontSize: 22,
    fontWeight: "bold",
    color: "#442c91",
    marginBottom: 10,
    marginTop: 30,
  },
  instructions: {
    fontSize: 16,
    color: "#442c91",
    textAlign: "center",
    marginBottom: 15,
  },
  cameraContainer: {
    width: "90%",
    aspectRatio: 3 / 4,
    borderRadius: 15,
    overflow: "hidden",
    marginBottom: 15,
    borderWidth: 2,
    borderColor: "#442c91",
  },
  camera: {
    flex: 1,
  },
  overlay: {
    flex: 1,
    justifyContent: "center",
    alignItems: "center",
  },
  faceFrame: {
    width: "70%",
    height: "65%",
    borderWidth: 2,
    borderColor: "#ffffff",
    borderRadius: 10,
  },
  controls: {
    flexDirection: "row",
    justifyContent: "space-around",
    alignItems: "center",
    width: "80%",
  },
  flipButton: {
    backgroundColor: "#442c91",
    padding: 10,
    borderRadius: 50,
    justifyContent: "center",
    alignItems: "center",
    width: 60,
    height: 60,
  },
  shutterButton: {
    backgroundColor: "#442c91",
    padding: 1,
    borderRadius: 50,
    alignItems: "center",
    justifyContent: "center",
    width: 70,
    height: 70,
  },
  status: {
    fontSize: 16,
    color: "#442c91",
    marginTop: 10,
  },
  loadingContainer: {
    flex: 1,
    justifyContent: "center",
    alignItems: "center",
  },
  loadingText: {
    fontSize: 18,
    color: "#442c91",
    marginTop: 10,
  },
});

export default FaceRecog;
