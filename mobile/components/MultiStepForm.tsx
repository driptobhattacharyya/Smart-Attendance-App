import React, { useState, useEffect, useContext } from 'react';
import { View, Text, StyleSheet, TouchableOpacity, Alert } from 'react-native';
import * as SecureStore from 'expo-secure-store';
import { AppContext } from "@/context/AppContext";
import BasicInfo from './BasicInfo';
import OfficeSelection from './OfficeSelection';
import BluetoothDeviceSelection from './BluetoothDeviceSelection';
import FaceRegistration from './FaceRegistration';
import SummaryScreen from './SummaryScreen';
import RequestForm from './request_form';
import { API_CONFIG } from "@/lib/config";

interface Step {
  id: number;
  title: string;
  component: React.ComponentType<any>;
}

interface MultiStepFormProps {
  isSignup?: boolean;
  onComplete?: () => void;
  onExit?: () => void;
}

const SIGNUP_STEPS: Step[] = [
  { id: 1, title: 'Basic Information', component: BasicInfo },
  { id: 2, title: 'Office Selection', component: OfficeSelection },
  { id: 3, title: 'Bluetooth Setup', component: BluetoothDeviceSelection },
  { id: 4, title: 'Face Registration', component: FaceRegistration },
  { id: 5, title: 'Summary', component: SummaryScreen }
];

const ATTENDANCE_STEPS: Step[] = [
  { id: 1, title: 'Face Recognition', component: FaceRegistration },
  { id: 2, title: 'Bluetooth Connection', component: BluetoothDeviceSelection },
  { id: 3, title: 'Form Data', component: RequestForm },
  { id: 4, title: 'Summary', component: SummaryScreen }
];

const MultiStepForm = ({ isSignup = false, onComplete, onExit }: MultiStepFormProps) => {
  const [currentStep, setCurrentStep] = useState(1);
  const [formData, setFormData] = useState<any>({});
  const [loading, setLoading] = useState(false);
  const context = useContext(AppContext);

  const STEPS = isSignup ? SIGNUP_STEPS : ATTENDANCE_STEPS;

  useEffect(() => {
    if (!isSignup) {
      loadSavedProgress();
    }
  }, []);

  const loadSavedProgress = async () => {
    try {
      const savedData = await SecureStore.getItemAsync('form_progress');
      if (savedData) {
        const { step, data, timestamp } = JSON.parse(savedData);
        const now = new Date().getTime();
        const savedTime = new Date(timestamp).getTime();
        const hoursDiff = (now - savedTime) / (1000 * 60 * 60);

        if (hoursDiff < 24) {
          setCurrentStep(step);
          setFormData(data);
        } else {
          await SecureStore.deleteItemAsync('form_progress');
        }
      }
    } catch (error) {
      console.error('Error loading saved progress:', error);
    }
  };

  const saveProgress = async () => {
    if (isSignup) return; // Don't save progress during signup
    
    try {
      const progressData = {
        step: currentStep,
        data: formData,
        timestamp: new Date().toISOString()
      };
      await SecureStore.setItemAsync('form_progress', JSON.stringify(progressData));
    } catch (error) {
      console.error('Error saving progress:', error);
    }
  };

  const handleNext = () => {
    if (currentStep < STEPS.length) {
      setCurrentStep(currentStep + 1);
      saveProgress();
    }
  };

  const handleBack = () => {
    if (currentStep > 1) {
      setCurrentStep(currentStep - 1);
      saveProgress();
    }
  };

  const handleExit = () => {
    if (isSignup) {
      Alert.alert(
        'Exit Signup',
        'Are you sure you want to exit? Your progress will be lost.',
        [
          {
            text: 'Exit',
            onPress: () => onExit?.()
          },
          {
            text: 'Cancel',
            style: 'cancel'
          }
        ]
      );
    } else {
      Alert.alert(
        'Exit Form',
        'Do you want to save your progress before exiting?',
        [
          {
            text: 'Save & Exit',
            onPress: async () => {
              await saveProgress();
              onExit?.();
            }
          },
          {
            text: 'Exit Without Saving',
            onPress: () => onExit?.()
          },
          {
            text: 'Cancel',
            style: 'cancel'
          }
        ]
      );
    }
  };

  const CurrentStepComponent = STEPS[currentStep - 1].component;

  return (
    <View style={styles.container}>
      <View style={[styles.header, { marginTop: 15 }]}>
        <Text style={styles.title}>
          {STEPS[currentStep - 1].title}
        </Text>
        <TouchableOpacity onPress={handleExit} style={styles.exitButton}>
          <Text style={styles.exitButtonText}>Exit</Text>
        </TouchableOpacity>
      </View>

      <View style={styles.progressBar}>
        {STEPS.map((step) => (
          <View
            key={step.id}
            style={[
              styles.progressStep,
              currentStep >= step.id && styles.progressStepActive
            ]}
          />
        ))}
      </View>

      <View style={styles.content}>
        <CurrentStepComponent
          formData={formData}
          setFormData={setFormData}
          onNext={handleNext}
          onBack={handleBack}
          onSubmit={() => onComplete?.()}
          isSignup={isSignup}
        />
      </View>

      {currentStep < STEPS.length && (
        <View style={styles.navigation}>
          {currentStep > 1 && (
            <TouchableOpacity onPress={handleBack} style={styles.navButton}>
              <Text style={styles.navButtonText}>Back</Text>
            </TouchableOpacity>
          )}
          <TouchableOpacity onPress={handleNext} style={styles.navButton}>
            <Text style={styles.navButtonText}>Next</Text>
          </TouchableOpacity>
        </View>
      )}
    </View>
  );
};

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: '#F8F4FF',
  },
  header: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    padding: 16,
    backgroundColor: '#fff',
    borderBottomWidth: 1,
    borderBottomColor: '#E6E0FF',
  },
  title: {
    fontSize: 20,
    fontWeight: '600',
    color: '#4A3B94',
  },
  exitButton: {
    padding: 8,
  },
  exitButtonText: {
    color: '#6A5ACD',
    fontSize: 16,
  },
  progressBar: {
    flexDirection: 'row',
    padding: 16,
    backgroundColor: '#fff',
  },
  progressStep: {
    flex: 1,
    height: 4,
    backgroundColor: '#E6E0FF',
    marginHorizontal: 4,
    borderRadius: 2,
  },
  progressStepActive: {
    backgroundColor: '#6A5ACD',
  },
  content: {
    flex: 1,
    padding: 16,
  },
  navigation: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    padding: 16,
    backgroundColor: '#fff',
    borderTopWidth: 1,
    borderTopColor: '#E6E0FF',
  },
  navButton: {
    backgroundColor: '#6A5ACD',
    paddingVertical: 12,
    paddingHorizontal: 24,
    borderRadius: 8,
    minWidth: 100,
    alignItems: 'center',
  },
  navButtonText: {
    color: '#fff',
    fontSize: 16,
    fontWeight: '600',
  },
});

export default MultiStepForm; 