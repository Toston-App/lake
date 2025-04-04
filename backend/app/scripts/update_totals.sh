#!/bin/bash

# Navigate to the appropriate directory
cd /app

# Run the update totals script
python -m app.update_totals

echo "Category and subcategory totals have been updated!"