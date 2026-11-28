program shop;

{$mode objfpc}{$H+}

uses
  cart;

var
  Basket: TCart;
  Tally: TIntTally;
  Sink: Int64;
  I: Integer;

procedure Run;
begin
  Sink := Sink + Basket.Total(30000000);
  Sink := Sink + Basket.Total('SKU-0001');
  Sink := Sink + Basket.Discount(30000000);
  Sink := Sink + Tally.Sum(30000000);
  Sink := Sink + Checksum(30000000);
end;

begin
  Basket := TCart.Create;
  Tally := TIntTally.Create;
  Sink := Primed;
  Run;
  for I := 1 to 20000000 do
    Sink := (Sink * 3 + I) mod 999917;
  WriteLn(Sink);
  Tally.Free;
  Basket.Free;
end.
