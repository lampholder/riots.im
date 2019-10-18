#!/bin/bash
rm -rf ./build_func
pip install -r requirements.txt --target ./build_func
cp riots.py ./build_func/
cp -r sites ./build_func/sites/
cd build_func
zip -r9 ../lambda.zip .
cd ..
rm -rf ./build_func
