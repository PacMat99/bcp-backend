from google.protobuf.internal import enum_type_wrapper as _enum_type_wrapper
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class SuspensionSetup(_message.Message):
    __slots__ = ("model", "travel", "spring", "pressure", "sag", "hsc", "lsc", "rebound", "tokens")
    class SpringType(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
        __slots__ = ()
        AIR: _ClassVar[SuspensionSetup.SpringType]
        COIL: _ClassVar[SuspensionSetup.SpringType]
    AIR: SuspensionSetup.SpringType
    COIL: SuspensionSetup.SpringType
    MODEL_FIELD_NUMBER: _ClassVar[int]
    TRAVEL_FIELD_NUMBER: _ClassVar[int]
    SPRING_FIELD_NUMBER: _ClassVar[int]
    PRESSURE_FIELD_NUMBER: _ClassVar[int]
    SAG_FIELD_NUMBER: _ClassVar[int]
    HSC_FIELD_NUMBER: _ClassVar[int]
    LSC_FIELD_NUMBER: _ClassVar[int]
    REBOUND_FIELD_NUMBER: _ClassVar[int]
    TOKENS_FIELD_NUMBER: _ClassVar[int]
    model: str
    travel: int
    spring: SuspensionSetup.SpringType
    pressure: int
    sag: int
    hsc: int
    lsc: int
    rebound: int
    tokens: int
    def __init__(self, model: _Optional[str] = ..., travel: _Optional[int] = ..., spring: _Optional[_Union[SuspensionSetup.SpringType, str]] = ..., pressure: _Optional[int] = ..., sag: _Optional[int] = ..., hsc: _Optional[int] = ..., lsc: _Optional[int] = ..., rebound: _Optional[int] = ..., tokens: _Optional[int] = ...) -> None: ...

class TireSetup(_message.Message):
    __slots__ = ("model", "setup_type", "pressure", "pressure_unit")
    MODEL_FIELD_NUMBER: _ClassVar[int]
    SETUP_TYPE_FIELD_NUMBER: _ClassVar[int]
    PRESSURE_FIELD_NUMBER: _ClassVar[int]
    PRESSURE_UNIT_FIELD_NUMBER: _ClassVar[int]
    model: str
    setup_type: str
    pressure: float
    pressure_unit: str
    def __init__(self, model: _Optional[str] = ..., setup_type: _Optional[str] = ..., pressure: _Optional[float] = ..., pressure_unit: _Optional[str] = ...) -> None: ...

class HardwareConfig(_message.Message):
    __slots__ = ("expected_sensors", "recording_freq")
    EXPECTED_SENSORS_FIELD_NUMBER: _ClassVar[int]
    RECORDING_FREQ_FIELD_NUMBER: _ClassVar[int]
    expected_sensors: int
    recording_freq: int
    def __init__(self, expected_sensors: _Optional[int] = ..., recording_freq: _Optional[int] = ...) -> None: ...

class WheelsConfig(_message.Message):
    __slots__ = ("rims", "material")
    RIMS_FIELD_NUMBER: _ClassVar[int]
    MATERIAL_FIELD_NUMBER: _ClassVar[int]
    rims: str
    material: str
    def __init__(self, rims: _Optional[str] = ..., material: _Optional[str] = ...) -> None: ...

class BikeConfiguration(_message.Message):
    __slots__ = ("rigid", "hardtail", "full", "wheels", "front_tire", "rear_tire", "hardware")
    RIGID_FIELD_NUMBER: _ClassVar[int]
    HARDTAIL_FIELD_NUMBER: _ClassVar[int]
    FULL_FIELD_NUMBER: _ClassVar[int]
    WHEELS_FIELD_NUMBER: _ClassVar[int]
    FRONT_TIRE_FIELD_NUMBER: _ClassVar[int]
    REAR_TIRE_FIELD_NUMBER: _ClassVar[int]
    HARDWARE_FIELD_NUMBER: _ClassVar[int]
    rigid: RigidGeometry
    hardtail: HardtailGeometry
    full: FullSuspensionGeometry
    wheels: WheelsConfig
    front_tire: TireSetup
    rear_tire: TireSetup
    hardware: HardwareConfig
    def __init__(self, rigid: _Optional[_Union[RigidGeometry, _Mapping]] = ..., hardtail: _Optional[_Union[HardtailGeometry, _Mapping]] = ..., full: _Optional[_Union[FullSuspensionGeometry, _Mapping]] = ..., wheels: _Optional[_Union[WheelsConfig, _Mapping]] = ..., front_tire: _Optional[_Union[TireSetup, _Mapping]] = ..., rear_tire: _Optional[_Union[TireSetup, _Mapping]] = ..., hardware: _Optional[_Union[HardwareConfig, _Mapping]] = ...) -> None: ...

class RigidGeometry(_message.Message):
    __slots__ = ()
    def __init__(self) -> None: ...

class HardtailGeometry(_message.Message):
    __slots__ = ("fork",)
    FORK_FIELD_NUMBER: _ClassVar[int]
    fork: SuspensionSetup
    def __init__(self, fork: _Optional[_Union[SuspensionSetup, _Mapping]] = ...) -> None: ...

class FullSuspensionGeometry(_message.Message):
    __slots__ = ("fork", "shock")
    FORK_FIELD_NUMBER: _ClassVar[int]
    SHOCK_FIELD_NUMBER: _ClassVar[int]
    fork: SuspensionSetup
    shock: SuspensionSetup
    def __init__(self, fork: _Optional[_Union[SuspensionSetup, _Mapping]] = ..., shock: _Optional[_Union[SuspensionSetup, _Mapping]] = ...) -> None: ...

class Config(_message.Message):
    __slots__ = ("sensor_count", "sample_rate")
    SENSOR_COUNT_FIELD_NUMBER: _ClassVar[int]
    SAMPLE_RATE_FIELD_NUMBER: _ClassVar[int]
    sensor_count: int
    sample_rate: int
    def __init__(self, sensor_count: _Optional[int] = ..., sample_rate: _Optional[int] = ...) -> None: ...
