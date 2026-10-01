#!/bin/bash
cd /workspace/medwork
G="gs -q -dNOPAUSE -dBATCH -sDEVICE=png16m -dTextAlphaBits=4 -dGraphicsAlphaBits=4"
$G -r300 -sOutputFile=q_fly-%d.png /workspace/medella_vector/flyer_A5_front_back_press.pdf
$G -r300 -sOutputFile=q_card-%d.png /workspace/medella_vector/business_card_front_back_press.pdf
if [ "$1" == "hi" ]; then
$G -r600 -sOutputFile=h_fly-%d.png /workspace/medella_vector/flyer_A5_front_back_press.pdf
$G -r600 -sOutputFile=h_card-%d.png /workspace/medella_vector/business_card_front_back_press.pdf
fi
