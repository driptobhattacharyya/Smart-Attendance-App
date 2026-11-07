import React, { useState, useEffect } from 'react';
import {
  View,
  Text,
  StyleSheet,
  TextInput,
  FlatList,
  TouchableOpacity,
  ActivityIndicator,
  Platform,
  Alert,
} from 'react-native';
import MapView, { Marker, Circle, PROVIDER_GOOGLE } from 'react-native-maps';
import * as Location from 'expo-location';
import { API_CONFIG } from "@/lib/config";
import { apiFetch } from "@/lib/api";

interface Office {
  office_id: string;
  name: string;
  latitude: number;
  longitude: number;
  radius: number;
  address: string;
}

interface OfficeSelectionProps {
  formData: any;
  setFormData: (data: any) => void;
  onNext: () => void;
}

const OfficeSelection = ({ formData, setFormData, onNext }: OfficeSelectionProps) => {
  const [offices, setOffices] = useState<Office[]>([]);
  const [filteredOffices, setFilteredOffices] = useState<Office[]>([]);
  const [searchQuery, setSearchQuery] = useState('');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedOffice, setSelectedOffice] = useState<Office | null>(null);
  const [userLocation, setUserLocation] = useState<Location.LocationObject | null>(null);

  useEffect(() => {
    const setup = async () => {
      await requestLocationPermission();
      await fetchOffices();
    };
    setup();
  }, []);

  const requestLocationPermission = async () => {
    try {
      const { status } = await Location.requestForegroundPermissionsAsync();
      if (status !== 'granted') {
        console.log('Location permission denied');
        return;
      }

      const location = await Location.getCurrentPositionAsync({
        accuracy: Location.Accuracy.Balanced,
      });
      setUserLocation(location);
    } catch (error) {
      console.error('Error getting location:', error);
    }
  };

  const fetchOffices = async () => {
    try {
      setLoading(true);
      setError(null);
      const data = await apiFetch(API_CONFIG.ENDPOINTS.GET_OFFICES, { token: '' });
      const list: Office[] = (Array.isArray(data) ? data : []).map((o: any) => ({
        office_id: o.id,
        name: o.name,
        latitude: o.latitude,
        longitude: o.longitude,
        radius: o.radius,
        address: o.address ?? '',
      }));
      setOffices(list);
      setFilteredOffices(list);
    } catch (error) {
      console.error('Error fetching offices:', error);
      setError(error instanceof Error ? error.message : 'Failed to load offices');
      setOffices([]);
      setFilteredOffices([]);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (searchQuery.trim() === '') {
      setFilteredOffices(offices);
    } else {
      const filtered = offices.filter(office =>
        office.name.toLowerCase().includes(searchQuery.toLowerCase()) ||
        office.address.toLowerCase().includes(searchQuery.toLowerCase())
      );
      setFilteredOffices(filtered);
    }
  }, [searchQuery, offices]);

  const handleOfficeSelect = (office: Office) => {
    setSelectedOffice(office);
    setFormData({
      ...formData,
      office_id: office.office_id,
      office_name: office.name,
      office_address: office.address
    });
  };

  const handleNext = () => {
    if (!selectedOffice) {
      Alert.alert('Error', 'Please select an office');
      return;
    }
    onNext();
  };

  if (loading) {
    return (
      <View style={styles.loadingContainer}>
        <ActivityIndicator size="large" color="#6A5ACD" />
        <Text style={styles.loadingText}>Loading offices...</Text>
      </View>
    );
  }

  if (error) {
    return (
      <View style={styles.errorContainer}>
        <Text style={styles.errorText}>{error}</Text>
        <TouchableOpacity style={styles.retryButton} onPress={fetchOffices}>
          <Text style={styles.retryButtonText}>Retry</Text>
        </TouchableOpacity>
      </View>
    );
  }

  const initialRegion = {
    latitude: userLocation?.coords.latitude || offices[0]?.latitude || 0,
    longitude: userLocation?.coords.longitude || offices[0]?.longitude || 0,
    latitudeDelta: 0.05,
    longitudeDelta: 0.05,
  };

  return (
    <View style={styles.container}>
      <TextInput
        style={styles.searchInput}
        placeholder="Search offices..."
        value={searchQuery}
        onChangeText={setSearchQuery}
        placeholderTextColor="#666"
      />

      <View style={styles.mapContainer}>
        <MapView
          style={styles.map}
          provider={PROVIDER_GOOGLE}
          initialRegion={initialRegion}
          showsUserLocation={true}
          showsMyLocationButton={true}
        >
          {filteredOffices.map((office) => (
            <React.Fragment key={office.office_id}>
              <Marker
                coordinate={{
                  latitude: office.latitude,
                  longitude: office.longitude,
                }}
                title={office.name}
                description={office.address}
                onPress={() => handleOfficeSelect(office)}
              />
              <Circle
                center={{
                  latitude: office.latitude,
                  longitude: office.longitude,
                }}
                radius={office.radius}
                strokeWidth={1}
                strokeColor="#6A5ACD"
                fillColor="rgba(106, 90, 205, 0.1)"
              />
            </React.Fragment>
          ))}
        </MapView>
      </View>

      <FlatList
        style={styles.list}
        data={filteredOffices}
        keyExtractor={(item) => item.office_id}
        renderItem={({ item }) => (
          <TouchableOpacity
            style={[
              styles.officeItem,
              selectedOffice?.office_id === item.office_id && styles.selectedOffice
            ]}
            onPress={() => handleOfficeSelect(item)}
          >
            <Text style={styles.officeName}>{item.name}</Text>
            <Text style={styles.officeAddress}>{item.address}</Text>
          </TouchableOpacity>
        )}
        ListEmptyComponent={
          <Text style={styles.emptyText}>
            {searchQuery ? 'No offices found matching your search' : 'No offices available'}
          </Text>
        }
      />

      <TouchableOpacity
        style={[styles.nextButton, !selectedOffice && styles.nextButtonDisabled]}
        onPress={handleNext}
        disabled={!selectedOffice}
      >
        <Text style={styles.nextButtonText}>Next</Text>
      </TouchableOpacity>
    </View>
  );
};

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: '#F8F4FF',
  },
  loadingContainer: {
    flex: 1,
    justifyContent: 'center',
    alignItems: 'center',
    backgroundColor: '#F8F4FF',
  },
  loadingText: {
    marginTop: 10,
    color: '#4A3B94',
    fontSize: 16,
  },
  errorContainer: {
    flex: 1,
    justifyContent: 'center',
    alignItems: 'center',
    backgroundColor: '#F8F4FF',
    padding: 20,
  },
  errorText: {
    color: '#FF3B30',
    fontSize: 16,
    textAlign: 'center',
    marginBottom: 20,
  },
  retryButton: {
    backgroundColor: '#6A5ACD',
    paddingHorizontal: 20,
    paddingVertical: 10,
    borderRadius: 8,
  },
  retryButtonText: {
    color: '#fff',
    fontSize: 16,
    fontWeight: '600',
  },
  searchInput: {
    margin: 16,
    padding: 12,
    backgroundColor: '#fff',
    borderRadius: 8,
    borderWidth: 1,
    borderColor: '#6A5ACD',
    color: '#4A3B94',
  },
  mapContainer: {
    height: 300,
    marginHorizontal: 16,
    borderRadius: 8,
    overflow: 'hidden',
  },
  map: {
    ...StyleSheet.absoluteFillObject,
  },
  list: {
    flex: 1,
    marginTop: 16,
    paddingHorizontal: 16,
  },
  officeItem: {
    backgroundColor: '#fff',
    padding: 16,
    borderRadius: 8,
    marginBottom: 8,
    borderWidth: 1,
    borderColor: '#E6E0FF',
  },
  selectedOffice: {
    borderColor: '#6A5ACD',
    backgroundColor: '#F0EDFF',
  },
  officeName: {
    fontSize: 16,
    fontWeight: '600',
    color: '#4A3B94',
    marginBottom: 4,
  },
  officeAddress: {
    fontSize: 14,
    color: '#666',
  },
  emptyText: {
    textAlign: 'center',
    color: '#666',
    marginTop: 20,
    fontSize: 16,
  },
  nextButton: {
    backgroundColor: '#6A5ACD',
    margin: 16,
    padding: 16,
    borderRadius: 8,
    alignItems: 'center',
  },
  nextButtonDisabled: {
    backgroundColor: '#B8B8B8',
  },
  nextButtonText: {
    color: '#fff',
    fontSize: 16,
    fontWeight: '600',
  },
});

export default OfficeSelection; 