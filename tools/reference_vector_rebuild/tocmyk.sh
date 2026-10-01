#!/bin/bash
# $1 in rgb pdf, $2 out cmyk pdf
gs -q -dNOPAUSE -dBATCH -dSAFER --permit-file-read=/usr/share/color/icc/ --permit-file-read=/workspace/medwork/ -sDEVICE=pdfwrite -dCompatibilityLevel=1.6 \
 -sColorConversionStrategy=CMYK -dProcessColorModel=/DeviceCMYK \
 -dRenderIntent=1 -dBlackPtComp=1 \
 -dDownsampleColorImages=false -dDownsampleGrayImages=false -dAutoFilterColorImages=false -dColorImageFilter=/DCTEncode \
 -dEmbedAllFonts=true -dSubsetFonts=true -dPreserveHalftoneInfo=false \
 -o "$2" -c "<< /ColorImageDict << /QFactor 0.15 /Blend 1 /HSamples [1 1 1 1] /VSamples [1 1 1 1] >> >> setdistillerparams" -f "$1"
