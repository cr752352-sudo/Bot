import logging
import json
import os

# Configure logging
def configure_logging():
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s',
        handlers=[
            logging.FileHandler('bot.log'),
            logging.StreamHandler()
        ]
    )

# Load configuration
def load_config(config_path):
    if not os.path.exists(config_path):
        logging.error("Configuration file not found!")
        return None
    with open(config_path, 'r') as f:
        config = json.load(f)
    logging.info("Configuration loaded successfully.")
    return config

# Utility commands
def utility_command_1():
    logging.info("Utility command 1 executed.")
    return "Result of command 1"


def utility_command_2():
    logging.info("Utility command 2 executed.")
    return "Result of command 2"

if __name__ == '__main__':
    configure_logging()
    config = load_config('config.json')
    if config:
        # Execute utility commands based on config or user input
        result1 = utility_command_1()
        result2 = utility_command_2()
        logging.info(f'Result 1: {result1}')
        logging.info(f'Result 2: {result2}')