# 1. Install everything
sudo apt-get update
sudo apt-get install -y ghc cabal-install supercollider sc3-plugins
cabal update
cabal install --lib tidal
echo 'Quarks.install("SuperDirt"); thisProcess.shutdown;' | sclang -D
cd ~ && git clone https://github.com/tidalcycles/Dirt-Samples.git

# 2. Test audio
sclang
# Type: s.boot;
# Type: { SinOsc.ar(440) * 0.3 }.play;  (you should hear a tone)
# Type: s.quit;

# 3. Run your app
#cd /home/moebius/Projects/youtube-stream-music-generation
#python app.py