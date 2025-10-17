const { app, BrowserWindow } = require('electron');
const path = require('path');

function createWindow() {
  const win = new BrowserWindow({
    width: 1200,
    height: 800,
    icon: path.join(__dirname, 'logo.png'), // Dein Icon
    webPreferences: {
      nodeIntegration: false
    }
  });

  win.loadURL('https://jds-appstore.de'); // URL der Web-App
}

app.whenReady().then(createWindow);

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit();
});
