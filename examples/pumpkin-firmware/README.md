# Open RealtimeAPI Embedded SDK

## Build

```sh
git submodule update --init --recursive
idf.py build flash monitor
```

Of course you can directly burn the compiled firmware.
```sh
cd firmware
pip3 install --upgrade esptool
esptool.py --chip esp32s3 -b 460800 --before default_reset --after hard_reset write_flash --flash_mode dio --flash_size 32MB --flash_freq 80m 0x0 bootloader/bootloader.bin 0x8000 partition_table/partition-table.bin 0x110000 openai-realtime.bin
```

Note: In order to preserve the production information of the device, be careful not to erase the entire flash.

## Usage

Configure wifi and openai key through the serial command line.


![console](console.png "console")

```sh
openai_api -k xxx
wifi_sta  -s  xxx -p xxx
```
