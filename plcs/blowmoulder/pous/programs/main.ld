PROGRAM main
  VAR
    Preform_At_Loader : BOOL AT %MX0.0 := FALSE; (* Sensor: a preform waits at the loader (end of the hopper chute) *)
    Preform_In_Oven : BOOL AT %MX0.1 := FALSE; (* Sensor: a preform sits in the oven pocket (ends LOAD, pocket empty before the next load) *)
    Arm_At_Oven : BOOL AT %MX0.2 := FALSE; (* Sensor: transfer arm home above the oven pocket, out of the mould (the mould only closes with it) *)
    Arm_At_Mould : BOOL AT %MX0.3 := FALSE; (* Sensor: transfer arm inside the open mould, the preform can be released *)
    Mould_Closed : BOOL AT %MX0.4 := FALSE; (* Sensor: mould halves closed and locked (blow only with it) *)
    Mould_Open : BOOL AT %MX0.5 := FALSE; (* Sensor: mould halves fully open (arm and ejector only with it) *)
    Rod_Up : BOOL AT %MX0.6 := FALSE; (* Sensor: stretch rod up, out of the bottle (the mould opens only with it) *)
    Rod_Down : BOOL AT %MX0.7 := FALSE; (* Sensor: stretch rod at the bottom of the mould, stretch finished *)
    Bottle_In_Mould : BOOL AT %MX4.0 := FALSE; (* Sensor: photo eye across the open mould, a preform or bottle is in it *)
    Bottle_Ejected : BOOL AT %MX4.1 := FALSE; (* Sensor: eye at the entry of C1, one pulse per bottle (BottleCount counts the rising edge) *)
    Hopper_Empty : BOOL AT %MX4.2 := FALSE; (* Sensor: no preforms left in the hopper *)
    Preform_Low : BOOL AT %MX4.3 := FALSE; (* Sensor: hopper low, refill soon (warning in FUXA only) *)
    C1_Full : BOOL AT %MX4.4 := FALSE; (* Sensor S1: C1 full (raw, filtered into Outfeed_Full) *)
    OvenTemp : INT AT %MW12 := 0; (* Analog sensor: preform surface temperature in the oven pocket, 0.1 degC (1050 = 105.0 degC) *)
    BlowPressure_Raw : INT AT %MW13 := 0; (* Analog sensor: blow pressure transducer, raw counts 0-27648 = 0-40.0 bar (below 0 = wire break) *)
    E_Stop_OK : BOOL AT %MX1.1 := FALSE; (* Interface: E-stop chain healthy (FALSE = stop everything at once) *)
    Heartbeat : BOOL AT %MX1.2 := FALSE; (* Interface: toggled every 1 s by the simulator, watched by the heartbeat watchdog *)
    Cmd : INT AT %MW0 := 0; (* HMI command: 1 Start, 2 Stop, 3 Reset, 4 Reset counter (PLC sets it back to 0 after reading) *)
    ModeReq : INT AT %MW1 := 0; (* HMI mode: 0 Stop, 1 Auto, 2 Manual *)
    OvenSetpoint : INT AT %MW30 := 1050; (* HMI setpoint: preform temperature, 0.1 degC *)
    OvenBand : INT AT %MW31 := 50; (* HMI setpoint: Oven_Ready window +- around OvenSetpoint, 0.1 degC *)
    Kp : INT AT %MW32 := 10; (* HMI setpoint: P gain of the lamp controller, % per degC *)
    Bias : INT AT %MW33 := 0; (* HMI setpoint: lamp power added to the P term, % (0 shows the P offset) *)
    BlowSetpoint : INT AT %MW34 := 350; (* HMI setpoint: high-blow pressure to reach, 0.1 bar *)
    Man_HopperFeed : BOOL AT %MX2.0 := FALSE; (* HMI manual button (hold-to-run): HopperFeed *)
    Man_Loader : BOOL AT %MX2.1 := FALSE; (* HMI manual button (hold-to-run): Loader *)
    Man_Arm : BOOL AT %MX2.2 := FALSE; (* HMI manual button (hold-to-run): ArmToMould *)
    Man_Gripper : BOOL AT %MX2.3 := FALSE; (* HMI manual button (hold-to-run): Gripper *)
    Man_MouldClose : BOOL AT %MX2.4 := FALSE; (* HMI manual button (toggle): each click flips Man_Mould_Closed *)
    Man_Rod : BOOL AT %MX2.5 := FALSE; (* HMI manual button (hold-to-run): StretchRod (only with the mould closed) *)
    Man_Ejector : BOOL AT %MX2.6 := FALSE; (* HMI manual button (hold-to-run): Ejector *)
    HopperFeed : BOOL AT %QX0.0 := FALSE; (* Output: hopper elevator / chute vibrator *)
    Loader : BOOL AT %QX0.1 := FALSE; (* Output: pushes the waiting preform into the oven pocket (spring return) *)
    ArmToMould : BOOL AT %QX0.2 := FALSE; (* Output: transfer arm, ON = into the mould, OFF = home above the oven *)
    Gripper : BOOL AT %QX0.3 := FALSE; (* Output: gripper on the arm, ON = closed *)
    MouldClose : BOOL AT %QX0.4 := FALSE; (* Output: mould halves, ON = closed and locked (seal-in, opens only with Pressure_Safe and Rod_Up) *)
    StretchRod : BOOL AT %QX0.5 := FALSE; (* Output: stretch rod, ON = down (only with the mould closed) *)
    PreBlowValve : BOOL AT %QX0.6 := FALSE; (* Output: low-pressure air during the stretch *)
    HighBlowValve : BOOL AT %QX0.7 := FALSE; (* Output: high-pressure air up to BlowSetpoint, then held *)
    ExhaustValve : BOOL AT %QX1.0 := FALSE; (* Output: vents the bottle, normally open (ON except STRETCH, BLOW, HOLD) *)
    Ejector : BOOL AT %QX1.1 := FALSE; (* Output: pushes the bottle out of the open mould onto C1 *)
    LampPower : INT AT %QW0 := 0; (* Analog output: IR lamp power 0-100 % (P controller) *)
    State : INT AT %MW50 := 0; (* Machine state: 0 Stopped, 1 Running, 2 Starved, 3 Blocked, 9 Fault *)
    Step : INT AT %MW51 := 0; (* Oven station: 0 IDLE, 10 FEED, 15 LOAD, 20 HEAT, 25 HOT, 900 FAULT *)
    MouldStep : INT AT %MW52 := 0; (* Mould station: 0 IDLE, 10 WAIT_PREFORM, 20-26 transfer, 30 CLOSE ... 80 EJECT, 900 FAULT *)
    BottleCount : INT AT %MW53 := 0; (* Bottles put onto C1 (rising edge of Bottle_Ejected), zeroed by Cmd = 4 *)
    Alm_NoPreforms : BOOL AT %MX5.0 := FALSE; (* Warning: hopper empty and the oven waits for a preform (Starved, not latched) *)
    Alm_HandlingJam : BOOL AT %MX5.1 := FALSE; (* Alarm: loader, arm or ejector did not reach its sensor in time (FAULT) *)
    Alm_OutfeedBlocked : BOOL AT %MX5.2 := FALSE; (* Warning: Blocked longer than BlockedWarn *)
    Alm_CommLost : BOOL AT %MX5.3 := FALSE; (* Alarm: heartbeat not toggled for 3 s (outputs off via Master_Enable) *)
    Alm_MouldFault : BOOL AT %MX5.4 := FALSE; (* Alarm: mould or stretch rod did not reach its position in time (FAULT) *)
    Alm_OvenOverTemp : BOOL AT %MX5.5 := FALSE; (* Alarm: OvenTemp >= OvenMax (FAULT in every mode, lamp power 0) *)
    Alm_OvenHeatTimeout : BOOL AT %MX5.6 := FALSE; (* Alarm: HEAT did not reach Preform_Hot within HeatTimeout (FAULT) *)
    Alm_BlowFault : BOOL AT %MX5.7 := FALSE; (* Alarm: BLOW did not reach BlowSetpoint within BlowTimeout (FAULT) *)
    Alm_ExhaustFault : BOOL AT %MX7.0 := FALSE; (* Alarm: EXHAUST did not reach Pressure_Safe within ExhaustTimeout (FAULT, mould stays closed) *)
    Alm_OverPressure : BOOL AT %MX7.1 := FALSE; (* Alarm: BlowPressure >= MaxPressure (FAULT) *)
    Alm_PressureSensor : BOOL AT %MX7.2 := FALSE; (* Alarm: BlowPressure_Raw outside RawMin..RawMax, wire break or over range (FAULT) *)
    Outfeed_Full : BOOL AT %MX3.0 := FALSE; (* Conditioned: C1_Full ON 2 s without a break, released after OFF 3 s *)
    Oven_Ready : BOOL AT %MX3.1 := FALSE; (* Window compare: OvenTemp within OvenSetpoint +- OvenBand *)
    Jog_Block : BOOL AT %MX3.2 := FALSE; (* Jog watchdog: a hold-to-run button held > 10 s, manual outputs ignored until released *)
    Preform_Hot : BOOL AT %MX3.3 := FALSE; (* Conditioned: preform inside Oven_Ready for SoakTime without a break (Soak_Timer.Q) *)
    Pressure_Reached : BOOL AT %MX3.4 := FALSE; (* Conditioned: BlowPressure >= BlowSetpoint *)
    Pressure_Safe : BOOL AT %MX3.5 := FALSE; (* Conditioned: BlowPressure < ExhaustLimit and the sensor in range (the mould may open) *)
    Starved : BOOL AT %MX3.6 := FALSE; (* Conditioned: no preform at the loader for StarvedDelay while the mould waits too -> State 2 *)
    Lamps_On : BOOL AT %MX3.7 := FALSE; (* Conditioned: Master_Enable AND Auto AND no FAULT AND Preform_In_Oven, enables LampPower *)
    BlowPressure : INT AT %MW41 := 0; (* Computed: BlowPressure_Raw scaled to 0.1 bar in DINT (Raw * 400 / 27648) *)
    OvenError : INT AT %MW42 := 0; (* Computed: OvenSetpoint - OvenTemp, 0.1 degC (input of the P controller) *)
    OvenMax : INT := 1300; (* Constant: over-temperature limit, 0.1 degC (130.0 degC, PET crystallises) *)
    ErrMax : INT := 1000; (* Constant: upper LIMIT on OvenError before the MUL (Kp * error stays inside INT) *)
    ErrMin : INT := -1000; (* Constant: lower LIMIT on OvenError before the MUL *)
    RawFull : DINT := 27648; (* Constant: raw counts at full scale (20 mA), DINT for the scaling MUL / DIV *)
    PressFull : DINT := 400; (* Constant: full-scale pressure, 0.1 bar (40.0 bar), DINT for the scaling MUL / DIV *)
    RawMin : INT := -1000; (* Constant: below this raw value = wire break (Alm_PressureSensor) *)
    RawMax : INT := 32511; (* Constant: above this raw value = over range (Alm_PressureSensor) *)
    ExhaustLimit : INT := 5; (* Constant: below this pressure (0.1 bar) the mould may open *)
    MaxPressure : INT := 400; (* Constant: over-pressure limit, 0.1 bar (40.0 bar) *)
    SoakTime : TIME := T#1s; (* Constant: preform inside Oven_Ready this long = heated through *)
    GripTime : TIME := T#300ms; (* Constant: gripper closes / opens *)
    HoldTime : TIME := T#1s; (* Constant: high pressure held, the PET cools against the mould *)
    MoveTimeout : TIME := T#3s; (* Constant: loader, arm, mould, rod, ejector must reach their sensor within this time *)
    HeatTimeout : TIME := T#15s; (* Constant: HEAT must reach Preform_Hot within this time *)
    BlowTimeout : TIME := T#2s; (* Constant: BLOW must reach BlowSetpoint within this time *)
    ExhaustTimeout : TIME := T#3s; (* Constant: EXHAUST must reach Pressure_Safe within this time *)
    StarvedDelay : TIME := T#2s; (* Constant: no preform at the loader this long = Starved *)
    BlockedWarn : TIME := T#2m; (* Constant: Blocked this long -> Alm_OutfeedBlocked *)
  END_VAR

{
  "name": "main",
  "rungs": [
    {
      "id": "rung_main_c26f1c80-a86e-4c92-829e-63d4b6758d95",
      "comment": "STAGE 0 TEST RUNG, delete after the %QW0 check: E_Stop_OK -> MOVE 42 -> LampPower (holding register 0?)",
      "defaultBounds": [
        300,
        100
      ],
      "reactFlowViewport": [
        598,
        134
      ],
      "nodes": [
        {
          "id": "left-rail-rung_main_c26f1c80-a86e-4c92-829e-63d4b6758d95",
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
            "numericId": "6467792",
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
          "id": "CONTACT_80cc3b53-9783-4c22-87db-d459621feb9e",
          "type": "contact",
          "position": {
            "x": 68,
            "y": 38
          },
          "data": {
            "handles": [
              {
                "glbPosition": {
                  "x": 68,
                  "y": 50
                },
                "relPosition": {
                  "x": 0,
                  "y": 12
                },
                "id": "input",
                "position": "left",
                "isConnectable": false,
                "type": "target",
                "style": {
                  "left": -3
                }
              },
              {
                "glbPosition": {
                  "x": 92,
                  "y": 50
                },
                "relPosition": {
                  "x": 24,
                  "y": 12
                },
                "id": "output",
                "position": "right",
                "isConnectable": false,
                "type": "source",
                "style": {
                  "right": -3
                }
              }
            ],
            "variant": "default",
            "inputHandles": [
              {
                "glbPosition": {
                  "x": 68,
                  "y": 50
                },
                "relPosition": {
                  "x": 0,
                  "y": 12
                },
                "id": "input",
                "position": "left",
                "isConnectable": false,
                "type": "target",
                "style": {
                  "left": -3
                }
              }
            ],
            "outputHandles": [
              {
                "glbPosition": {
                  "x": 92,
                  "y": 50
                },
                "relPosition": {
                  "x": 24,
                  "y": 12
                },
                "id": "output",
                "position": "right",
                "isConnectable": false,
                "type": "source",
                "style": {
                  "right": -3
                }
              }
            ],
            "inputConnector": {
              "glbPosition": {
                "x": 68,
                "y": 50
              },
              "relPosition": {
                "x": 0,
                "y": 12
              },
              "id": "input",
              "position": "left",
              "isConnectable": false,
              "type": "target",
              "style": {
                "left": -3
              }
            },
            "outputConnector": {
              "glbPosition": {
                "x": 92,
                "y": 50
              },
              "relPosition": {
                "x": 24,
                "y": 12
              },
              "id": "output",
              "position": "right",
              "isConnectable": false,
              "type": "source",
              "style": {
                "right": -3
              }
            },
            "numericId": "3087156",
            "variable": {
              "name": "E_Stop_OK",
              "class": "local",
              "type": {
                "definition": "base-type",
                "value": "BOOL"
              },
              "location": "%MX1.1",
              "documentation": "Interface: E-stop chain healthy (FALSE = stop everything at once)",
              "debug": false,
              "id": ""
            },
            "executionOrder": 0,
            "draggable": true,
            "selectable": true,
            "deletable": true
          },
          "width": 24,
          "height": 24,
          "measured": {
            "width": 24,
            "height": 24
          },
          "draggable": true,
          "selectable": true,
          "selected": false,
          "dragging": false
        },
        {
          "id": "BLOCK_6ca6e828-cfd7-42a1-a245-cfc8fda9dd4f",
          "type": "block",
          "position": {
            "x": 257,
            "y": 14
          },
          "data": {
            "handles": [
              {
                "glbPosition": {
                  "x": 257,
                  "y": 50
                },
                "relPosition": {
                  "x": 0,
                  "y": 36
                },
                "id": "EN",
                "position": "left",
                "type": "target",
                "isConnectable": false,
                "style": {
                  "top": 36,
                  "left": 0
                }
              },
              {
                "glbPosition": {
                  "x": 257,
                  "y": 90
                },
                "relPosition": {
                  "x": 0,
                  "y": 76
                },
                "id": "IN",
                "position": "left",
                "type": "target",
                "isConnectable": false,
                "style": {
                  "top": 76,
                  "left": 0
                }
              },
              {
                "glbPosition": {
                  "x": 335,
                  "y": 50
                },
                "relPosition": {
                  "x": 78,
                  "y": 36
                },
                "id": "ENO",
                "position": "right",
                "type": "source",
                "isConnectable": false,
                "style": {
                  "top": 36,
                  "right": 0
                }
              },
              {
                "glbPosition": {
                  "x": 335,
                  "y": 90
                },
                "relPosition": {
                  "x": 78,
                  "y": 76
                },
                "id": "OUT",
                "position": "right",
                "type": "source",
                "isConnectable": false,
                "style": {
                  "top": 76,
                  "right": 0
                }
              }
            ],
            "inputHandles": [
              {
                "glbPosition": {
                  "x": 257,
                  "y": 50
                },
                "relPosition": {
                  "x": 0,
                  "y": 36
                },
                "id": "EN",
                "position": "left",
                "type": "target",
                "isConnectable": false,
                "style": {
                  "top": 36,
                  "left": 0
                }
              },
              {
                "glbPosition": {
                  "x": 257,
                  "y": 90
                },
                "relPosition": {
                  "x": 0,
                  "y": 76
                },
                "id": "IN",
                "position": "left",
                "type": "target",
                "isConnectable": false,
                "style": {
                  "top": 76,
                  "left": 0
                }
              }
            ],
            "outputHandles": [
              {
                "glbPosition": {
                  "x": 335,
                  "y": 50
                },
                "relPosition": {
                  "x": 78,
                  "y": 36
                },
                "id": "ENO",
                "position": "right",
                "type": "source",
                "isConnectable": false,
                "style": {
                  "top": 36,
                  "right": 0
                }
              },
              {
                "glbPosition": {
                  "x": 335,
                  "y": 90
                },
                "relPosition": {
                  "x": 78,
                  "y": 76
                },
                "id": "OUT",
                "position": "right",
                "type": "source",
                "isConnectable": false,
                "style": {
                  "top": 76,
                  "right": 0
                }
              }
            ],
            "inputConnector": {
              "glbPosition": {
                "x": 257,
                "y": 50
              },
              "relPosition": {
                "x": 0,
                "y": 36
              },
              "id": "EN",
              "position": "left",
              "type": "target",
              "isConnectable": false,
              "style": {
                "top": 36,
                "left": 0
              }
            },
            "outputConnector": {
              "glbPosition": {
                "x": 335,
                "y": 50
              },
              "relPosition": {
                "x": 78,
                "y": 36
              },
              "id": "ENO",
              "position": "right",
              "type": "source",
              "isConnectable": false,
              "style": {
                "top": 36,
                "right": 0
              }
            },
            "numericId": "3709009",
            "variant": {
              "name": "MOVE",
              "type": "function",
              "language": "st",
              "variables": [
                {
                  "name": "EN",
                  "class": "input",
                  "type": {
                    "definition": "generic-type",
                    "value": "BOOL"
                  }
                },
                {
                  "name": "ENO",
                  "class": "output",
                  "type": {
                    "definition": "generic-type",
                    "value": "BOOL"
                  }
                },
                {
                  "name": "OUT",
                  "class": "output",
                  "type": {
                    "definition": "generic-type",
                    "value": "ANY"
                  }
                },
                {
                  "name": "IN",
                  "class": "input",
                  "type": {
                    "definition": "generic-type",
                    "value": "ANY"
                  }
                }
              ],
              "body": "",
              "documentation": "",
              "extensible": false,
              "category": "Selection"
            },
            "variable": {
              "name": ""
            },
            "executionOrder": 0,
            "executionControl": true,
            "lockExecutionControl": false,
            "connectedVariables": [
              {
                "handleId": "IN",
                "type": "input",
                "variable": {
                  "name": "42"
                }
              },
              {
                "handleId": "OUT",
                "type": "output",
                "variable": {
                  "name": "LampPower",
                  "class": "local",
                  "type": {
                    "definition": "base-type",
                    "value": "INT"
                  },
                  "location": "%QW0",
                  "initialValue": "0",
                  "documentation": "Analog output: IR lamp power 0-100 % (P controller)",
                  "debug": false
                }
              }
            ],
            "draggable": true,
            "selectable": true,
            "deletable": true
          },
          "width": 78,
          "height": 100,
          "measured": {
            "width": 78,
            "height": 100
          },
          "draggable": true,
          "selectable": true,
          "selected": false,
          "dragging": false
        },
        {
          "id": "VARIABLE_39e10cad-22da-467e-99c4-53a6f1bc5a0f",
          "type": "variable",
          "position": {
            "x": 147,
            "y": 74
          },
          "width": 80,
          "height": 32,
          "measured": {
            "width": 80,
            "height": 32
          },
          "data": {
            "handles": [
              {
                "glbPosition": {
                  "x": 227,
                  "y": 90
                },
                "relPosition": {
                  "x": 80,
                  "y": 16
                },
                "id": "output",
                "position": "right",
                "isConnectable": false,
                "type": "source"
              }
            ],
            "inputHandles": [],
            "outputHandles": [
              {
                "glbPosition": {
                  "x": 227,
                  "y": 90
                },
                "relPosition": {
                  "x": 80,
                  "y": 16
                },
                "id": "output",
                "position": "right",
                "isConnectable": false,
                "type": "source"
              }
            ],
            "outputConnector": {
              "glbPosition": {
                "x": 227,
                "y": 90
              },
              "relPosition": {
                "x": 80,
                "y": 16
              },
              "id": "output",
              "position": "right",
              "isConnectable": false,
              "type": "source"
            },
            "numericId": "7908459",
            "variable": {
              "name": "42"
            },
            "executionOrder": 0,
            "variant": "input",
            "block": {
              "id": "BLOCK_6ca6e828-cfd7-42a1-a245-cfc8fda9dd4f",
              "handleId": "IN",
              "variableType": {
                "name": "IN",
                "class": "input",
                "type": {
                  "definition": "generic-type",
                  "value": "ANY"
                }
              }
            },
            "draggable": false,
            "selectable": true,
            "deletable": false
          },
          "deletable": false,
          "selectable": true,
          "draggable": false,
          "selected": false,
          "dragging": false
        },
        {
          "id": "VARIABLE_01985459-3a65-4ddd-801d-d8bb710ea1cd",
          "type": "variable",
          "position": {
            "x": 365,
            "y": 74
          },
          "width": 80,
          "height": 32,
          "measured": {
            "width": 80,
            "height": 32
          },
          "data": {
            "handles": [
              {
                "glbPosition": {
                  "x": 365,
                  "y": 90
                },
                "relPosition": {
                  "x": 0,
                  "y": 16
                },
                "id": "input",
                "position": "left",
                "isConnectable": false,
                "type": "target"
              }
            ],
            "inputHandles": [
              {
                "glbPosition": {
                  "x": 365,
                  "y": 90
                },
                "relPosition": {
                  "x": 0,
                  "y": 16
                },
                "id": "input",
                "position": "left",
                "isConnectable": false,
                "type": "target"
              }
            ],
            "outputHandles": [],
            "inputConnector": {
              "glbPosition": {
                "x": 365,
                "y": 90
              },
              "relPosition": {
                "x": 0,
                "y": 16
              },
              "id": "input",
              "position": "left",
              "isConnectable": false,
              "type": "target"
            },
            "numericId": "7362840",
            "variable": {
              "name": "LampPower",
              "class": "local",
              "type": {
                "definition": "base-type",
                "value": "INT"
              },
              "location": "%QW0",
              "initialValue": "0",
              "documentation": "Analog output: IR lamp power 0-100 % (P controller)",
              "debug": false
            },
            "executionOrder": 0,
            "variant": "output",
            "block": {
              "id": "BLOCK_6ca6e828-cfd7-42a1-a245-cfc8fda9dd4f",
              "handleId": "OUT",
              "variableType": {
                "name": "OUT",
                "class": "output",
                "type": {
                  "definition": "generic-type",
                  "value": "ANY"
                }
              }
            },
            "draggable": false,
            "selectable": true,
            "deletable": false
          },
          "deletable": false,
          "selectable": true,
          "draggable": false,
          "selected": false,
          "dragging": false
        },
        {
          "id": "right-rail-rung_main_c26f1c80-a86e-4c92-829e-63d4b6758d95",
          "type": "powerRail",
          "position": {
            "x": 595,
            "y": 30
          },
          "data": {
            "handles": [
              {
                "glbPosition": {
                  "x": 598,
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
                  "x": 598,
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
                "x": 598,
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
            "numericId": "2266607",
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
          "id": "e_left-rail-rung_main_c26f1c80-a86e-4c92-829e-63d4b6758d95_CONTACT_80cc3b53-9783-4c22-87db-d459621feb9e__left-rail_input",
          "source": "left-rail-rung_main_c26f1c80-a86e-4c92-829e-63d4b6758d95",
          "target": "CONTACT_80cc3b53-9783-4c22-87db-d459621feb9e",
          "sourceHandle": "left-rail",
          "targetHandle": "input"
        },
        {
          "id": "e_CONTACT_80cc3b53-9783-4c22-87db-d459621feb9e_BLOCK_6ca6e828-cfd7-42a1-a245-cfc8fda9dd4f__output_EN",
          "source": "CONTACT_80cc3b53-9783-4c22-87db-d459621feb9e",
          "target": "BLOCK_6ca6e828-cfd7-42a1-a245-cfc8fda9dd4f",
          "sourceHandle": "output",
          "targetHandle": "EN"
        },
        {
          "id": "e_VARIABLE_39e10cad-22da-467e-99c4-53a6f1bc5a0f_BLOCK_6ca6e828-cfd7-42a1-a245-cfc8fda9dd4f__output_IN",
          "source": "VARIABLE_39e10cad-22da-467e-99c4-53a6f1bc5a0f",
          "target": "BLOCK_6ca6e828-cfd7-42a1-a245-cfc8fda9dd4f",
          "sourceHandle": "output",
          "targetHandle": "IN"
        },
        {
          "id": "e_BLOCK_6ca6e828-cfd7-42a1-a245-cfc8fda9dd4f_VARIABLE_01985459-3a65-4ddd-801d-d8bb710ea1cd__OUT_input",
          "source": "BLOCK_6ca6e828-cfd7-42a1-a245-cfc8fda9dd4f",
          "target": "VARIABLE_01985459-3a65-4ddd-801d-d8bb710ea1cd",
          "sourceHandle": "OUT",
          "targetHandle": "input"
        },
        {
          "id": "e_BLOCK_6ca6e828-cfd7-42a1-a245-cfc8fda9dd4f_right-rail-rung_main_c26f1c80-a86e-4c92-829e-63d4b6758d95__ENO_right-rail",
          "source": "BLOCK_6ca6e828-cfd7-42a1-a245-cfc8fda9dd4f",
          "target": "right-rail-rung_main_c26f1c80-a86e-4c92-829e-63d4b6758d95",
          "sourceHandle": "ENO",
          "targetHandle": "right-rail"
        }
      ],
      "selectedNodes": [],
      "handleBranches": []
    }
  ]
}
END_PROGRAM