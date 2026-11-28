unit cart;

{$mode objfpc}{$H+}

interface

type
  TCart = class
  private
    FSeed: Int64;
  public
    function Total(Rounds: Integer): Int64; overload;
    function Total(const Code: string): Int64; overload;
    function Discount(Rounds: Integer): Int64;
  end;

  generic TTally<T> = class
    function Sum(Rounds: Integer): Int64;
  end;

  TIntTally = specialize TTally<Integer>;

function Checksum(Rounds: Integer): Int64;

var
  Primed: Int64;

implementation

function Mix(Value: Int64): Int64;
begin
  Result := (Value * 31 + 7) mod 1000003;
end;

function TCart.Total(Rounds: Integer): Int64;
var
  Acc: Int64;

  procedure Accumulate(Count: Integer);
  var
    I: Integer;
  begin
    for I := 1 to Count do
      Acc := (Acc * 17 + I) mod 999983;
  end;

begin
  Acc := FSeed;
  Accumulate(Rounds);
  Result := Acc;
end;

function TCart.Total(const Code: string): Int64;
var
  I, J: Integer;
begin
  Result := 0;
  for J := 1 to 4000000 do
    for I := 1 to Length(Code) do
      Result := (Result + Ord(Code[I]) * J) mod 999979;
end;

// Pascal names have no case: the declaration says Discount.
function tcart.DISCOUNT(Rounds: Integer): Int64;
var
  I: Integer;
begin
  Result := 1;
  for I := 1 to Rounds do
    Result := Mix(Result + I);
end;

function TTally.Sum(Rounds: Integer): Int64;
var
  I: Integer;
begin
  Result := 0;
  for I := 1 to Rounds do
    Result := (Result + I * 13) mod 999961;
end;

function Checksum(Rounds: Integer): Int64;
var
  I: Integer;
begin
  Result := 5381;
  for I := 1 to Rounds do
    Result := (Result * 33 + I) mod 999953;
end;

var
  K: Integer;

initialization
  Primed := 0;
  for K := 1 to 20000000 do
    Primed := (Primed * 7 + K) mod 999931;
end.
