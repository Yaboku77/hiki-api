import sys
import os

# Insert the current directory into the system path
sys.path.insert(0, os.path.dirname(__file__))

# Import the 'app' object from your app.py file
from app import app as application
