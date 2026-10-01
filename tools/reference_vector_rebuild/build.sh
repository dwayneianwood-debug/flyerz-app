#!/bin/bash
set -e
cd /workspace/medwork; source /workspace/medenv/bin/activate
python pdf.py >/dev/null
./tocmyk.sh rgb_flyer_A5_front_back.pdf /workspace/medella_vector/flyer_A5_front_back_press.pdf
./tocmyk.sh rgb_business_card_front_back.pdf /workspace/medella_vector/business_card_front_back_press.pdf
./render.sh $1
python tiles.py
