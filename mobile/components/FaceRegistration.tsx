import React, { useRef, useState } from 'react';
import {
  View,
  Text,
  StyleSheet,
  TouchableOpacity,
  ActivityIndicator,
  Image,
} from 'react-native';
import {
  Camera,
  CameraType,
  CameraView,
  useCameraPermissions,
} from 'expo-camera';
import * as FileSystem from 'expo-file-system';
import MaterialIcons from '@expo/vector-icons/MaterialIcons';
import { API_CONFIG } from "@/lib/config";

interface FaceRegistrationProps {
  formData: any;
  setFormData: (data: any) => void;
  onNext: () => void;
}

const FaceRegistration = ({ formData, setFormData, onNext }: FaceRegistrationProps) => {
  const [isLoading, setIsLoading] = useState(false);
  const [facing, setFacing] = useState<CameraType>('front');
  const cameraRef = useRef<CameraView | null>(null);
  const [permission, requestPermission] = useCameraPermissions();
  const [photo, setPhoto] = useState<any>(null);

  if (!permission) {
    return <View />; // Camera permissions are still loading
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
    setFacing((current) => (current === 'back' ? 'front' : 'back'));
  }

  const handleCapture = async () => {
    try {
      if (cameraRef.current) {
        const options = {
          quality: 0.9,
          exif: false,
        };
        const takePhoto = await cameraRef.current.takePictureAsync(options);
        if (!takePhoto) {
          throw new Error('Failed to capture photo');
        }
        setPhoto(takePhoto);
        
        // Store the photo in formData
        setFormData({
          ...formData,
          photo: {
            uri: takePhoto.uri,
            name: 'photo.jpg',
            type: 'image/jpeg',
          },
        });
        
        onNext();
      }
    } catch (error) {
      console.error('Error during photo capture:', error);
      alert('An error occurred while capturing the image. Please try again.');
    }
  };

  return (
    <View style={styles.container}>
      <Text style={styles.header}>Face Registration</Text>
      <Text style={styles.instructions}>
        Position your face in the frame and click the button below to capture your photo.
      </Text>
      {isLoading ? (
        <View style={styles.loadingContainer}>
          <ActivityIndicator size="large" color="#442c91" />
          <Text style={styles.loadingText}>Processing photo, please wait...</Text>
        </View>
      ) : (
        <>
          <View style={styles.cameraContainer}>
            <CameraView ref={cameraRef} style={styles.camera} facing={facing}>
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
              onPress={handleCapture}
            >
              <MaterialIcons name="camera" size={45} color="white" />
            </TouchableOpacity>
          </View>
          <Text style={styles.status}>Status: Ready to capture</Text>
        </>
      )}
    </View>
  );
};

const styles = StyleSheet.create({
  container: {
    flex: 1,
    padding: 20,
    backgroundColor: '#f4eefe',
    alignItems: 'center',
  },
  message: {
    textAlign: 'center',
    paddingBottom: 10,
  },
  permissionButton: {
    marginTop: 10,
    backgroundColor: '#442c91',
    padding: 10,
    borderRadius: 5,
  },
  permissionText: {
    color: 'white',
    fontWeight: 'bold',
    fontSize: 16,
  },
  header: {
    fontSize: 22,
    fontWeight: 'bold',
    color: '#442c91',
    marginBottom: 10,
    marginTop: 30,
  },
  instructions: {
    fontSize: 16,
    color: '#442c91',
    textAlign: 'center',
    marginBottom: 15,
  },
  cameraContainer: {
    width: '90%',
    aspectRatio: 3 / 4,
    borderRadius: 15,
    overflow: 'hidden',
    marginBottom: 15,
    borderWidth: 2,
    borderColor: '#442c91',
  },
  camera: {
    flex: 1,
  },
  overlay: {
    flex: 1,
    justifyContent: 'center',
    alignItems: 'center',
  },
  faceFrame: {
    width: '70%',
    height: '65%',
    borderWidth: 2,
    borderColor: '#ffffff',
    borderRadius: 10,
  },
  controls: {
    flexDirection: 'row',
    justifyContent: 'space-around',
    alignItems: 'center',
    width: '80%',
  },
  flipButton: {
    backgroundColor: '#442c91',
    padding: 10,
    borderRadius: 50,
    justifyContent: 'center',
    alignItems: 'center',
    width: 60,
    height: 60,
  },
  shutterButton: {
    backgroundColor: '#442c91',
    padding: 10,
    borderRadius: 50,
    justifyContent: 'center',
    alignItems: 'center',
    width: 80,
    height: 80,
  },
  status: {
    marginTop: 20,
    fontSize: 16,
    color: '#442c91',
  },
  loadingContainer: {
    flex: 1,
    justifyContent: 'center',
    alignItems: 'center',
  },
  loadingText: {
    marginTop: 10,
    fontSize: 16,
    color: '#442c91',
  },
});

export default FaceRegistration; 