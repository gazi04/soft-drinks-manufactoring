PROGRAM main
  VAR
    SyrupLevel : INT AT %MW12 := 0; (* Analog sensor: syrup tank level, L (low -> Alm_LowSyrup) *)
    MixLevel : INT AT %MW13 := 0; (* Analog sensor: mixing tank level, L (dosing, agitator interlock, overflow) *)
    ProductTemp : INT AT %MW14 := 0; (* Analog sensor: product temperature in the mixing tank, 0.1 degC (40 = 4.0 degC) *)
    CO2Pressure : INT AT %MW15 := 0; (* Analog sensor: carbonator pressure, 0.01 bar (350 = 3.50 bar) *)
    BufferLevel : INT AT %MW16 := 0; (* Analog sensor: buffer tank level, L (new batch when low; Product_Available) *)
    MixHighHigh : BOOL AT %MX0.0 := FALSE; (* Sensor: high-high level switch on the mixing tank (~980 L), hardware backup for MixLevel *)
    E_Stop_OK : BOOL AT %MX1.1 := FALSE; (* Interface: E-stop chain healthy (FALSE = stop everything at once) *)
    Heartbeat : BOOL AT %MX1.2 := FALSE; (* Interface: toggled every 1 s by the simulator, watched by the heartbeat watchdog *)
    Product_Available : BOOL AT %MX6.0 := FALSE; (* Interface to the filler: buffer holds product (hysteresis on BufferLevel), copied to filler %MX1.0 *)
    Cmd : INT AT %MW0 := 0; (* HMI command: 1 Start, 2 Stop, 3 Reset (PLC sets it back to 0 after reading) *)
    ModeReq : INT AT %MW1 := 0; (* HMI mode: 0 Stop, 1 Auto, 2 Manual *)
    WaterBatch : INT AT %MW30 := 500; (* HMI setpoint: water per batch, L *)
    SyrupRatio : INT AT %MW31 := 55; (* HMI setpoint: water : syrup ratio x10 (55 = 5.5:1) *)
    MixTime : INT AT %MW32 := 20; (* HMI setpoint: agitator run time in MIX, s *)
    CoolSetpoint : INT AT %MW33 := 40; (* HMI setpoint: target product temperature, 0.1 degC *)
    TempHyst : INT AT %MW34 := 5; (* HMI setpoint: hysteresis band for cooling, 0.1 degC *)
    PressSetpoint : INT AT %MW35 := 350; (* HMI setpoint: target CO2 pressure, 0.01 bar *)
    PressHyst : INT AT %MW36 := 20; (* HMI setpoint: hysteresis band for the CO2 valve (CO2_Demand), 0.01 bar *)
    BufferLow : INT AT %MW37 := 800; (* HMI setpoint: start a new batch when BufferLevel < BufferLow, L *)
    BufferMin : INT AT %MW38 := 100; (* HMI setpoint: minimum buffer level for Product_Available, L *)
    CarbTime : INT AT %MW39 := 10; (* HMI setpoint: pressure must hold this long without the CO2 valve, s *)
    Man_WaterPump : BOOL AT %MX2.0 := FALSE; (* HMI manual button (hold-to-run): WaterPump *)
    Man_SyrupPump : BOOL AT %MX2.1 := FALSE; (* HMI manual button (hold-to-run): SyrupPump *)
    Man_Agitator : BOOL AT %MX2.2 := FALSE; (* HMI manual button (hold-to-run): Agitator, only above AgitatorMin (interlock) *)
    Man_Cooling : BOOL AT %MX2.3 := FALSE; (* HMI manual button (hold-to-run): CoolingValve *)
    Man_CO2 : BOOL AT %MX2.4 := FALSE; (* HMI manual button (hold-to-run): CO2Valve, only when cold (interlock) *)
    Man_Transfer : BOOL AT %MX2.5 := FALSE; (* HMI manual button (hold-to-run): TransferPump *)
    WaterPump : BOOL AT %QX0.0 := FALSE; (* Output: mains water -> mixing tank *)
    SyrupPump : BOOL AT %QX0.1 := FALSE; (* Output: syrup tank -> mixing tank *)
    Agitator : BOOL AT %QX0.2 := FALSE; (* Output: mixer motor (interlock: MixLevel >= AgitatorMin) *)
    CoolingValve : BOOL AT %QX0.3 := FALSE; (* Output: cooling jacket of the mixing tank *)
    CO2Valve : BOOL AT %QX0.4 := FALSE; (* Output: CO2 into the carbonator (interlock: Temp_OK) *)
    TransferPump : BOOL AT %QX0.5 := FALSE; (* Output: mixing tank -> buffer tank *)
    State : INT AT %MW50 := 0; (* Status for the HMI: 0 Stopped, 1 Running, 2 Starved, 3 Blocked, 9 Fault *)
    Step : INT AT %MW51 := 0; (* Sequencer step: 0 IDLE, 5 WAIT_DEMAND, 10 DOSE_WATER, 20 DOSE_SYRUP, 30 MIX, 40 COOL, 50 CARBONATE, 55 WAIT_BUFFER, 60 TRANSFER, 900 FAULT *)
    BatchCount : INT AT %MW53 := 0; (* Status for the HMI: batches transferred to the buffer *)
    Alm_Overflow : BOOL AT %MX5.0 := FALSE; (* Alarm (latched): MixLevel >= MixMax OR MixHighHigh OR BufferLevel >= BufferMax -> FAULT *)
    Alm_OverPressure : BOOL AT %MX5.1 := FALSE; (* Alarm (latched): CO2Pressure >= PressMax -> FAULT *)
    Alm_CoolTimeout : BOOL AT %MX5.2 := FALSE; (* Alarm (latched): Temp_OK not reached within CoolTimeout in COOL -> FAULT *)
    Alm_CommLost : BOOL AT %MX5.3 := FALSE; (* Alarm (latched): heartbeat not toggled for 3 s, switches Master_Enable off (same bit in every PLC) *)
    Alm_LowSyrup : BOOL AT %MX5.4 := FALSE; (* Warning (not latched): SyrupLevel too low for the next batch, State Starved, clears itself after refill *)
    Alm_DoseTimeout : BOOL AT %MX5.5 := FALSE; (* Alarm (latched): DOSE_WATER / DOSE_SYRUP level not reached within DoseTimeout -> FAULT *)
    SyrupTarget : INT AT %MW40 := 0; (* Computed: WaterBatch x 10 / SyrupRatio, L (MUL then DIV) *)
    DoseTarget : INT AT %MW41 := 0; (* Computed: WaterBatch + SyrupTarget, L (level at the end of DOSE_SYRUP) *)
    Temp_OK : BOOL AT %MX3.0 := FALSE; (* Hysteresis latch: set at ProductTemp <= CoolSetpoint, reset at > CoolSetpoint + TempHyst *)
    CO2_Demand : BOOL AT %MX3.1 := FALSE; (* Hysteresis latch: set at CO2Pressure < PressSetpoint - PressHyst, reset at >= PressSetpoint *)
    Jog_Block : BOOL AT %MX3.2 := FALSE; (* Jog watchdog: a manual button held > 10 s, manual outputs ignored until all Man_* are released *)
    Batch_Dosed : BOOL AT %MX3.3 := FALSE; (* Water and syrup of the batch are dosed: set at the end of DOSE_SYRUP, reset at the end of TRANSFER *)
    MixMax : INT := 950; (* Constant: mixing tank high limit, L (tank 1000 L) *)
    MixEmpty : INT := 5; (* Constant: mixing tank counts as empty below this, L *)
    AgitatorMin : INT := 100; (* Constant: propeller covered from this level on, L *)
    BufferMax : INT := 1950; (* Constant: buffer tank high limit, L (tank 2000 L) *)
    PressMax : INT := 600; (* Constant: over-pressure limit, 0.01 bar (6.00 bar) *)
    AvailBand : INT := 20; (* Constant: hysteresis band of Product_Available, L *)
    CoolTimeout : TIME := T#60s; (* Constant: COOL must reach Temp_OK within this time (simulator time) *)
    DoseTimeout : TIME := T#60s; (* Constant: DOSE_WATER / DOSE_SYRUP must reach their level within this time (simulator time) *)
  END_VAR

{
  "name": "main",
  "rungs": [
    {
      "id": "rung_main_dac1fa84-d7f7-4866-b2e2-382f861f97e6",
      "comment": "STAGE 0 TEST RUNG (empty). Only here so the project compiles and uploads; replaced by SECTION 1 MODES AND SAFETY in stage 1.",
      "defaultBounds": [
        300,
        100
      ],
      "reactFlowViewport": [
        303,
        120
      ],
      "nodes": [
        {
          "id": "left-rail-rung_main_dac1fa84-d7f7-4866-b2e2-382f861f97e6",
          "type": "powerRail",
          "position": {
            "x": 0,
            "y": 30
          },
          "data": {
            "handles": [
              {
                "glbPosition": {
                  "x": 3,
                  "y": 50
                },
                "relPosition": {
                  "x": 3,
                  "y": 20
                },
                "id": "left-rail",
                "position": "right",
                "type": "source",
                "isConnectable": false,
                "style": {
                  "top": 20,
                  "right": -3
                }
              }
            ],
            "inputHandles": [],
            "outputHandles": [
              {
                "glbPosition": {
                  "x": 3,
                  "y": 50
                },
                "relPosition": {
                  "x": 3,
                  "y": 20
                },
                "id": "left-rail",
                "position": "right",
                "type": "source",
                "isConnectable": false,
                "style": {
                  "top": 20,
                  "right": -3
                }
              }
            ],
            "outputConnector": {
              "glbPosition": {
                "x": 3,
                "y": 50
              },
              "relPosition": {
                "x": 3,
                "y": 20
              },
              "id": "left-rail",
              "position": "right",
              "type": "source",
              "isConnectable": false,
              "style": {
                "top": 20,
                "right": -3
              }
            },
            "numericId": "4826703",
            "variant": "left",
            "variable": {
              "name": ""
            },
            "executionOrder": 0,
            "draggable": false,
            "selectable": false,
            "deletable": false
          },
          "width": 3,
          "height": 40,
          "measured": {
            "width": 3,
            "height": 40
          },
          "draggable": false,
          "selectable": false,
          "selected": false,
          "dragging": false
        },
        {
          "id": "right-rail-rung_main_dac1fa84-d7f7-4866-b2e2-382f861f97e6",
          "type": "powerRail",
          "position": {
            "x": 300,
            "y": 30
          },
          "data": {
            "handles": [
              {
                "glbPosition": {
                  "x": 297,
                  "y": 50
                },
                "relPosition": {
                  "x": 3,
                  "y": 20
                },
                "id": "right-rail",
                "position": "left",
                "type": "target",
                "isConnectable": false,
                "style": {
                  "top": 20,
                  "left": -3
                }
              }
            ],
            "inputHandles": [
              {
                "glbPosition": {
                  "x": 297,
                  "y": 50
                },
                "relPosition": {
                  "x": 3,
                  "y": 20
                },
                "id": "right-rail",
                "position": "left",
                "type": "target",
                "isConnectable": false,
                "style": {
                  "top": 20,
                  "left": -3
                }
              }
            ],
            "outputHandles": [],
            "inputConnector": {
              "glbPosition": {
                "x": 297,
                "y": 50
              },
              "relPosition": {
                "x": 3,
                "y": 20
              },
              "id": "right-rail",
              "position": "left",
              "type": "target",
              "isConnectable": false,
              "style": {
                "top": 20,
                "left": -3
              }
            },
            "numericId": "828088",
            "variant": "right",
            "variable": {
              "name": ""
            },
            "executionOrder": 0,
            "draggable": false,
            "selectable": false,
            "deletable": false
          },
          "width": 3,
          "height": 40,
          "measured": {
            "width": 3,
            "height": 40
          },
          "draggable": false,
          "selectable": false,
          "selected": false,
          "dragging": false
        }
      ],
      "edges": [
        {
          "id": "e_left-rail-rung_main_dac1fa84-d7f7-4866-b2e2-382f861f97e6_right-rail-rung_main_dac1fa84-d7f7-4866-b2e2-382f861f97e6",
          "source": "left-rail-rung_main_dac1fa84-d7f7-4866-b2e2-382f861f97e6",
          "target": "right-rail-rung_main_dac1fa84-d7f7-4866-b2e2-382f861f97e6",
          "sourceHandle": "left-rail",
          "targetHandle": "right-rail",
          "type": "smoothstep"
        }
      ],
      "selectedNodes": [],
      "handleBranches": []
    }
  ]
}
END_PROGRAM