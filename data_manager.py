import os
import json
import logging
from typing import Optional, Any, Dict

class DataManager:
    def __init__(self, data_path: str, default_config: Dict[str, Any]):
        self.data_path = data_path
        self.default_config = default_config
        self.data: Dict[str, Any] = {}
        self.load_data()

    def load_data(self):
        if not os.path.exists(self.data_path):
            logging.info(f"Data file not found at {self.data_path}. Creating with default config.")
            self.data = self.default_config
            try:
                with open(self.data_path, 'w', encoding='utf-8') as f:
                    json.dump(self.default_config, f, indent=4, ensure_ascii=False)
            except IOError as e:
                logging.error(f"Error creating data file {self.data_path}: {e}")
                # If file creation fails, still use default_config in memory
                self.data = self.default_config
        else:
            try:
                with open(self.data_path, 'r', encoding='utf-8') as f:
                    self.data = json.load(f)
            except FileNotFoundError:
                logging.error(f"Data file not found at {self.data_path} during read. This should not happen if os.path.exists was true.")
                self.data = self.default_config
            except json.JSONDecodeError as e:
                logging.error(f"Error decoding JSON from {self.data_path}: {e}. Using default config.")
                self.data = self.default_config
            except Exception as e:
                logging.error(f"An unexpected error occurred while loading data from {self.data_path}: {e}. Using default config.")
                self.data = self.default_config

    def save_data(self):
        try:
            with open(self.data_path, 'w', encoding='utf-8') as f:
                json.dump(self.data, f, indent=2, ensure_ascii=False)
            logging.info(f"Data saved to {self.data_path}")
        except IOError as e:
            logging.error(f"Error writing data to {self.data_path}: {e}")
        except Exception as e:
            logging.error(f"An unexpected error occurred while saving data to {self.data_path}: {e}")

    def get_data(self, key: str, default: Optional[Any] = None) -> Any:
        return self.data.get(key, default)

    def update_data(self, key: str, value: Any):
        self.data[key] = value
        self.save_data()

    def get_all_data(self) -> Dict[str, Any]:
        return self.data.copy()
