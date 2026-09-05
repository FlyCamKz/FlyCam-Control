# FlyCam branding assets

## FlyCam

`res/Images/flycam_logo.png` was extracted without redrawing from the Word 97-2003
file supplied by the user (`FlyCam Logo .doc`). The source image is a transparent
438 x 187 PNG.

The application, dispatcher dashboard, installer and Windows icon use only this
FlyCam mark.

`src/FlyCamSplashScreen.cc` displays the same mark together with the full
product name while the desktop application initializes. It uses a raster Qt
window so it does not initialize the QML graphics engine prematurely. The
splash screen contains no university name or logo.
